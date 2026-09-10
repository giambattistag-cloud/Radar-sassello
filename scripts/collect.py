"""Hourly DPC SRT1 collector. UTC intervals, native pixels, explicit missing data."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform
from rasterio.windows import Window
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
HOUR = 3_600_000
DAY = 24 * HOUR
CENTER = (8.48736, 44.47917)
RADIUS = 25_000
API = 'https://radar-api.protezionecivile.it'


def iso(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat().replace('+00:00', 'Z')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == '.gz' else raw)
    tmp.replace(path)


def read_json(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def sample_path(root, t):
    dt = datetime.fromtimestamp(t / 1000, timezone.utc)
    return root / 'archive' / dt.strftime('%Y/%m/%d/%H.json.gz')


def session():
    s = requests.Session()
    s.headers.update({'Origin': 'https://radar.protezionecivile.it',
                      'Referer': 'https://radar.protezionecivile.it/',
                      'User-Agent': 'Radar-Sassello/1.0'})
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['GET', 'POST'])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    return s


def latest(s, product):
    r = s.get(f'{API}/findLastProductByType', params={'type': product}, timeout=(15, 40))
    r.raise_for_status()
    products = r.json().get('lastProducts', [])
    if not products:
        raise ValueError(f'Nessun prodotto {product} disponibile')
    return int(products[0]['time'])


def download(s, product, t):
    r = s.post(f'{API}/downloadProduct', json={'productType': product, 'productDate': t},
               timeout=(15, 40))
    if r.status_code == 404:
        return None
    r.raise_for_status()
    # Signed URLs last 15 minutes; used immediately, never persisted or logged.
    url = r.json().get('url')
    if not isinstance(url, str) or not url.startswith('https://'):
        raise ValueError('Risposta DPC senza URL HTTPS')
    r = s.get(url, timeout=(15, 60))
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def make_grid(ds):
    if not ds.crs or not ds.crs.is_projected:
        raise ValueError('SRT1: attesa griglia radar in coordinate metriche')
    if not (900 <= abs(ds.transform.a) <= 1100 and 900 <= abs(ds.transform.e) <= 1100):
        raise ValueError('Risoluzione SRT1 cambiata: verificare metadati prima di proseguire')
    x, y = transform('EPSG:4326', ds.crs, [CENTER[0]], [CENTER[1]])
    col, row = ~ds.transform * (x[0], y[0])
    n = math.ceil(RADIUS / abs(ds.transform.a)) + 1
    win = Window(math.floor(col)-n, math.floor(row)-n, 2*n+1, 2*n+1)
    if win.col_off < 0 or win.row_off < 0 or win.col_off + win.width > ds.width or win.row_off + win.height > ds.height:
        raise ValueError('Sassello non coperto dalla griglia sorgente')
    tr = ds.window_transform(win)
    w, h = int(win.width), int(win.height)
    corners = []
    for rr in range(h+1):
        xs, ys = zip(*(tr * (cc, rr) for cc in range(w+1)))
        lons, lats = transform(ds.crs, 'EPSG:4326', xs, ys)
        corners.extend([[round(lat, 6), round(lon, 6)] for lon, lat in zip(lons, lats)])
    active = []
    for rr in range(h):
        for cc in range(w):
            xx, yy = tr * (cc+.5, rr+.5)
            active.append(math.hypot(xx-x[0], yy-y[0]) <= RADIUS)
    return {'version': 1, 'width': w, 'height': h, 'crs': ds.crs.to_wkt(),
            'transform': list(tr)[:6], 'corners': corners, 'active': active,
            'center': [CENTER[1], CENTER[0]], 'radiusKm': 25,
            'resolutionM': abs(ds.transform.a), 'temperatureResolution': 'circa 2 km, interpolata da stazioni'}


def decode(raw, grid=None, product='SRT1'):
    with MemoryFile(raw) as mem, mem.open() as ds:
        grid = grid or make_grid(ds)
        data = ds.read(1, masked=True).astype('float32').filled(np.nan)
        data = data * ds.scales[0] + ds.offsets[0]
        # Negative SRT1 is missing, not zero. Preserve zero as observed dry weather.
        valid = np.isfinite(data) & ((data >= 0) & (data <= 500) if product == 'SRT1' else (data >= -60) & (data <= 60))
        data[~valid] = np.nan
        out = np.full((grid['height'], grid['width']), np.nan, dtype='float32')
        reproject(data, out, src_transform=ds.transform, src_crs=ds.crs,
                  dst_transform=Affine(*grid['transform']), dst_crs=grid['crs'],
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest)
        out = out.ravel()
        out[~np.array(grid['active'])] = np.nan
        return grid, [round(float(v), 2) if np.isfinite(v) else None for v in out]


def timeline(root, end):
    records = []
    for t in range(end-239*HOUR, end+1, HOUR):
        path = sample_path(root, t)
        if path.exists():
            item = read_json(path)
            if item['time'] != t:
                raise ValueError(f'Timestamp archivio errato: {path}')
            records.append(item)
    return records


def summarize(records, grid, end, hours=240):
    size = grid['width'] * grid['height']
    total = np.zeros(size); weighted = np.zeros(size); count = np.zeros(size, dtype=int)
    last = np.full(size, np.nan)
    seen = set()
    for item in sorted(records, key=lambda x: x['time']):
        t = item['time']
        if t in seen:
            raise ValueError('Campione orario duplicato')
        seen.add(t)
        if t % HOUR:
            raise ValueError('SRT1 non allineato alle ore UTC: possibile sovrapposizione')
        if not end-hours*HOUR < t <= end:
            continue
        a = np.array([np.nan if v is None else v for v in item['rain']], dtype=float)
        if a.size != size:
            raise ValueError('Griglia archivio non coerente')
        valid = np.isfinite(a)
        count += valid
        amount = np.nan_to_num(a)
        total += amount
        # Age refers to the midpoint of the one-hour accumulation interval.
        weighted += amount * ((end-t+HOUR/2)/DAY)
        last[valid & (a >= 1)] = t
    return {'rainMm': [round(float(total[i]), 2) if count[i] else None for i in range(size)],
            'ageDays': [round(float(weighted[i]/total[i]), 2) if total[i] > 0 else None for i in range(size)],
            'validHours': count.tolist(),
            'lastSignificantRain': [int(v) if np.isfinite(v) else None for v in last]}


def publish(root, grid, end, errors):
    records = timeline(root, end)
    summary = summarize(records, grid, end)
    payload = {'schemaVersion': 1, 'generatedAt': iso(int(datetime.now(timezone.utc).timestamp()*1000)),
               'windowEnd': end, 'windowStart': end-240*HOUR,
               'latestRainTime': max((r['time'] for r in records), default=None),
               'expectedHours': 240, 'availableHours': len(records),
               'source': 'Radar-DPC — Dipartimento della Protezione Civile',
               'license': 'CC-BY-SA 4.0', 'errors': errors,
               'grid': grid, 'summary': summary, 'timeline': records}
    write_json(root/'dist/data/latest.json', payload)
    # Fixed non-overlapping ten-day archive, including quality and explicit gaps.
    state_path = root/'archive/state.json'
    state = read_json(state_path) if state_path.exists() else {'anchor': end, 'nextSnapshot': end+10*DAY}
    while end >= state['nextSnapshot']:
        stop = state['nextSnapshot']
        past = timeline(root, stop)
        name = datetime.fromtimestamp(stop/1000, timezone.utc).strftime('%Y-%m-%dT%H')+'.json'
        snapshot = {**payload, 'windowEnd': stop, 'windowStart': stop-10*DAY,
                    'latestRainTime': max((r['time'] for r in past), default=None),
                    'availableHours': len(past), 'timeline': past,
                    'summary': summarize(past, grid, stop), 'snapshot': True}
        write_json(root/'dist/data/snapshots'/name, snapshot)
        state['nextSnapshot'] += 10*DAY
    write_json(state_path, state)
    snapshots = sorted(p.name for p in (root/'dist/data/snapshots').glob('*.json'))
    write_json(root/'dist/data/snapshots.json', snapshots)
    return payload


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--lookback-hours', type=int, default=24)
    p.add_argument('--max-downloads', type=int, default=24)
    p.add_argument('--offline', action='store_true')
    args = p.parse_args()
    root = args.root
    end = int(datetime.now(timezone.utc).timestamp()*1000)//HOUR*HOUR
    grid_path = root/'archive/grid.json'
    grid = read_json(grid_path) if grid_path.exists() else None
    errors = []; saved = 0
    if not args.offline:
        s = session()
        try:
            available = min(latest(s, 'SRT1')//HOUR*HOUR, end)
            try:
                temp_available = latest(s, 'TEMP')//HOUR*HOUR
            except Exception:
                temp_available = 0
                errors.append('Temperatura non disponibile dal servizio DPC')
            # Start from the newest interval to guarantee useful output early.
            start = end-(min(24, max(1, args.lookback_hours))-1)*HOUR
            candidates = [t for t in range(available, start-1, -HOUR)
                          if not sample_path(root, t).exists() or read_json(sample_path(root,t)).get('temperature') is None]
            for t in candidates[:max(1, args.max_downloads)]:
                path = sample_path(root, t)
                item = read_json(path) if path.exists() else None
                try:
                    if item is None:
                        raw = download(s, 'SRT1', t)
                        if raw is None:
                            errors.append(f'SRT1 assente: {iso(t)}'); continue
                        grid, rain = decode(raw, grid)
                        write_json(grid_path, grid)
                        item = {'time': t, 'rain': rain, 'temperature': None}
                        # Checkpoint rain before any secondary temperature request.
                        write_json(path, item)
                        saved += 1
                    if item.get('temperature') is None and t <= temp_available:
                        try:
                            raw_temp = download(s, 'TEMP', t)
                            if raw_temp is not None:
                                _, item['temperature'] = decode(raw_temp, grid, 'TEMP')
                                write_json(path, item)
                        except Exception:
                            errors.append(f'Temperatura assente: {iso(t)}')
                    print(f'Archiviato {iso(t)}', flush=True)
                except Exception as e:
                    # Exceptions may contain pre-signed URLs: don't log their text.
                    errors.append(f'Acquisizione fallita: {iso(t)} ({type(e).__name__})')
        except Exception as e:
            errors.append(f'Servizio DPC non raggiungibile ({type(e).__name__})')
    if grid is None:
        raise SystemExit('Nessuna griglia radar disponibile: raccolta non avviata')
    payload = publish(root, grid, end, errors)
    print(f"Nuove ore: {saved}; ore presenti: {payload['availableHours']}/240", flush=True)
    for err in errors:
        print(err, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(f"## Radar Sassello\nOre archiviate nella finestra: {payload['availableHours']}/240.\n")
            f.write(f"Ultimo dato: {iso(payload['latestRainTime']) if payload['latestRainTime'] else 'assente'}.\n")
    if not records_fresh(payload, end):
        raise SystemExit('Ultima pioggia misurata assente o più vecchia di 3 ore; archivio preservato')


def records_fresh(payload, end):
    t = payload['latestRainTime']
    return t is not None and end-t <= 3*HOUR


if __name__ == '__main__':
    main()
