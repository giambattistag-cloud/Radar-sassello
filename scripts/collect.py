"""DPC rainfall collector: hourly SRT1 totals plus five-minute SRI intensity."""
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
RADIUS = 10_000
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


def sri_path(root, t):
    dt = datetime.fromtimestamp(t / 1000, timezone.utc)
    return root / 'archive' / 'sri' / dt.strftime('%Y/%m/%d/%H%M.json.gz')


def model_path(root, t):
    dt = datetime.fromtimestamp(t / 1000, timezone.utc)
    return root / 'archive' / 'model' / dt.strftime('%Y/%m/%d/%H.json.gz')


def aspect_path(root):
    return root / 'archive' / 'aspect.json'


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
            'center': [CENTER[1], CENTER[0]], 'radiusKm': 10,
            'resolutionM': abs(ds.transform.a), 'temperatureResolution': 'circa 2 km, interpolata da stazioni'}


def decode(raw, grid=None, product='SRT1'):
    with MemoryFile(raw) as mem, mem.open() as ds:
        grid = grid or make_grid(ds)
        data = ds.read(1, masked=True).astype('float32').filled(np.nan)
        data = data * ds.scales[0] + ds.offsets[0]
        # Negative SRT1 is missing, not zero. Preserve zero as observed dry weather.
        valid = np.isfinite(data) & ((data >= 0) & (data <= 500) if product in {'SRT1', 'SRI'} else (data >= -60) & (data <= 60))
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
    for t in range(end-335*HOUR, end+1, HOUR):
        path = sample_path(root, t)
        if path.exists():
            item = read_json(path)
            if item['time'] != t:
                raise ValueError(f'Timestamp archivio errato: {path}')
            records.append(item)
    return records


def sri_timeline(root, end, hours=24):
    """Read the recent five-minute intensity samples, kept separately."""
    records = []
    start = end - hours * HOUR
    first = (start // (5 * 60 * 1000)) * (5 * 60 * 1000)
    for t in range(first + 5 * 60 * 1000, end + 1, 5 * 60 * 1000):
        path = sri_path(root, t)
        if path.exists():
            item = read_json(path)
            if item.get('time') == t:
                records.append(item)
    return records


def model_timeline(root, end, hours=336):
    records = []
    start = end - hours * HOUR
    for t in range(start + HOUR, end + 1, HOUR):
        path = model_path(root, t)
        if path.exists():
            item = read_json(path)
            if item.get('time') == t:
                records.append(item)
    return records


def update_model(s, root, end, errors, grid):
    """Fetch recent model history at 4 km sample points; map to native cells."""
    existing = model_timeline(root, end, hours=24)
    if existing and any(isinstance(x.get('solar'), list) and
                        any(v is not None for v in x['solar']) and
                        end-x['time'] < 2*HOUR for x in existing):
        return
    fields = {'temperature': 'temperature_2m', 'humidity': 'relative_humidity_2m',
              'solar': 'shortwave_radiation', 'cloud': 'cloud_cover',
              'wind': 'wind_speed_10m', 'et': 'et0_fao_evapotranspiration',
              'soilTemperature': 'soil_temperature_0cm', 'soilMoisture': 'soil_moisture_0_to_1cm'}
    w, h = grid['width'], grid['height']
    groups = {}
    for i, active in enumerate(grid['active']):
        if active:
            groups.setdefault((i//w//4, i%w//4), []).append(i)
    points = []
    for indices in groups.values():
        i = indices[len(indices)//2]; row, col = divmod(i, w)
        a = row*(w+1)+col
        b, d = grid['corners'][a], grid['corners'][a+w+2]
        points.append(((b[0]+d[0])/2, (b[1]+d[1])/2))
    try:
        r = s.get('https://api.open-meteo.com/v1/forecast', params={
            'latitude': ','.join(str(round(p[0],6)) for p in points),
            'longitude': ','.join(str(round(p[1],6)) for p in points),
            'past_days': 14, 'forecast_days': 1, 'timeformat': 'unixtime',
            'hourly': ','.join(fields.values()), 'timezone': 'UTC'}, timeout=(15, 60))
        r.raise_for_status()
        bodies = r.json()
        if isinstance(bodies, dict): bodies = [bodies]
        if len(bodies) != len(points): raise ValueError('Coordinate del modello incomplete')
        records = {}
        for body, indices in zip(bodies, groups.values()):
            hourly = body.get('hourly', {})
            for j, stamp in enumerate(hourly.get('time', [])):
                t = int(stamp)*1000
                if not end-336*HOUR < t <= end: continue
                item = records.setdefault(t, {'time': t, 'source': 'Open-Meteo forecast, storico modelli',
                    **{field: [None]*(w*h) for field in fields}})
                for field, source in fields.items():
                    values = hourly.get(source, [])
                    value = values[j] if j < len(values) else None
                    if value is not None and math.isfinite(value):
                        for i in indices: item[field][i] = value
        if not any(any(v is not None for v in item['solar']) for item in records.values()):
            raise ValueError('Irraggiamento assente')
        for t, item in records.items(): write_json(model_path(root, t), item)
    except Exception as e:
        errors.append(f'Modello sole/umidità non disponibile ({type(e).__name__})')


def update_aspect(s, root, grid, errors):
    """Derive a south-weighted terrain exposure score from a DEM."""
    path = aspect_path(root)
    if path.exists():
        return
    w, h = grid['width'], grid['height']
    centers = []
    for row in range(h):
        for col in range(w):
            a = row * (w + 1) + col
            b = grid['corners'][a]; d = grid['corners'][a + w + 2]
            centers.append(((b[0] + d[0]) / 2, (b[1] + d[1]) / 2))
    elevations = [None] * (w * h)
    try:
        # Open-Meteo accepts coordinate lists. Keep requests modest for URL size.
        needed = set()
        for i, active in enumerate(grid['active']):
            if active:
                row, col = divmod(i, w)
                needed.update([i, row*w+max(0,col-1), row*w+min(w-1,col+1),
                               max(0,row-1)*w+col, min(h-1,row+1)*w+col])
        needed = sorted(needed)
        for start in range(0, len(needed), 80):
            ids = needed[start:start+80]
            batch = [centers[i] for i in ids]
            r = s.get('https://api.open-meteo.com/v1/elevation', params={
                'latitude': ','.join(str(round(x[0], 6)) for x in batch),
                'longitude': ','.join(str(round(x[1], 6)) for x in batch)}, timeout=(15, 40))
            r.raise_for_status()
            values = r.json().get('elevation', [])
            if len(values) != len(ids): raise ValueError('DEM incompleto')
            for i, value in zip(ids, values): elevations[i] = value
        score = [None] * (w * h)
        for row in range(h):
            for col in range(w):
                i = row * w + col
                if not grid['active'][i] or elevations[i] is None:
                    continue
                left = elevations[row * w + max(0, col - 1)]
                right = elevations[row * w + min(w - 1, col + 1)]
                up = elevations[max(0, row - 1) * w + col]
                down = elevations[min(h - 1, row + 1) * w + col]
                if None in (left, right, up, down):
                    continue
                dzx = (right - left) / 2
                dzy = (down - up) / 2
                slope = math.hypot(dzx, dzy)
                if slope < 0.5:
                    score[i] = 0.5
                    continue
                # Aspect bearing: 0 north, 90 east, 180 south, 270 west.
                bearing = (math.degrees(math.atan2(-dzx, dzy)) + 360) % 360
                score[i] = round(0.5 + 0.5 * math.cos(math.radians(bearing - 180)), 4)
        write_json(path, {'source': 'Open-Meteo elevation / DEM', 'score': score,
                          'legend': '0 nord, 0.5 est/ovest o pianura, 1 sud'})
    except Exception as e:
        errors.append(f'Esposizione del terreno non disponibile ({type(e).__name__})')


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
    summary = summarize(records, grid, end, hours=336)
    sri_end = int(datetime.now(timezone.utc).timestamp()*1000)//300000*300000
    sri_records = sri_timeline(root, sri_end, hours=24)
    model_records = model_timeline(root, end, hours=336)
    aspect = read_json(aspect_path(root)).get('score') if aspect_path(root).exists() else None
    payload = {'schemaVersion': 1, 'generatedAt': iso(int(datetime.now(timezone.utc).timestamp()*1000)),
               'windowEnd': end, 'windowStart': end-336*HOUR,
               'latestRainTime': max((r['time'] for r in records), default=None),
               'expectedHours': 336, 'availableHours': len(records),
               'source': 'Radar-DPC — Dipartimento della Protezione Civile',
               'license': 'CC-BY-SA 4.0', 'errors': errors,
               'grid': grid, 'summary': summary, 'timeline': records,
               'recentSri': sri_records,
               'modelTimeline': model_records,
               'aspect': aspect,
               'sriResolution': '5 minuti; intensità mm/h; raccolta ogni 5 minuti',
               'radiusKm': 10}
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
                    'summary': summarize(past, grid, stop, hours=336), 'snapshot': True}
        write_json(root/'dist/data/snapshots'/name, snapshot)
        state['nextSnapshot'] += 10*DAY
    write_json(state_path, state)
    snapshots = sorted(p.name for p in (root/'dist/data/snapshots').glob('*.json'))
    write_json(root/'dist/data/snapshots.json', snapshots)
    return payload


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--lookback-hours', type=int, default=336)
    p.add_argument('--max-downloads', type=int, default=6)
    p.add_argument('--max-sri-downloads', type=int, default=2)
    p.add_argument('--offline', action='store_true')
    args = p.parse_args()
    root = args.root
    end = int(datetime.now(timezone.utc).timestamp()*1000)//HOUR*HOUR
    grid_path = root/'archive/grid.json'
    grid = read_json(grid_path) if grid_path.exists() else None
    # Preserve array indices and every archived rainfall sample when narrowing
    # the operational area. Only the active mask changes, not the grid geometry.
    if grid is not None and grid.get('radiusKm') != 10:
        tr = Affine(*grid['transform'])
        xs, ys = transform('EPSG:4326', grid['crs'], [CENTER[0]], [CENTER[1]])
        grid['active'] = [math.hypot(*(v-c for v,c in zip(
            tr * (col+.5, row+.5), (xs[0], ys[0])))) <= RADIUS
            for row in range(grid['height']) for col in range(grid['width'])]
        grid['radiusKm'] = 10
        write_json(grid_path, grid)
    errors = []; saved = 0
    if not args.offline:
        s = session()
        try:
            available = min(latest(s, 'SRT1')//HOUR*HOUR, end)
            try:
                sri_available = min(latest(s, 'SRI')//(5*60*1000)*(5*60*1000), int(datetime.now(timezone.utc).timestamp()*1000)//(5*60*1000)*(5*60*1000))
            except Exception:
                sri_available = 0
                errors.append('Prodotto SRI a 5 minuti non disponibile')
            try:
                temp_available = latest(s, 'TEMP')//HOUR*HOUR
            except Exception:
                temp_available = 0
                errors.append('Temperatura non disponibile dal servizio DPC')
            # Start from the newest interval to guarantee useful output early.
            start = end-(min(336, max(1, args.lookback_hours))-1)*HOUR
            candidates = [t for t in range(available, start-1, -HOUR)
                          if not sample_path(root, t).exists()]
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
            # SRI is an instantaneous intensity in mm/h, refreshed every five
            # minutes. Store each sample as a separate record; it is integrated
            # only for the current-hour detail and never added to SRT1 totals.
            if sri_available:
                sri_start = sri_available - 24 * HOUR
                sri_candidates = [t for t in range(sri_available, sri_start, -5 * 60 * 1000)
                                  if not sri_path(root, t).exists()]
                for t in sri_candidates[:max(1, args.max_sri_downloads)]:
                    try:
                        raw = download(s, 'SRI', t)
                        if raw is None:
                            continue
                        grid, intensity = decode(raw, grid, 'SRI')
                        write_json(sri_path(root, t), {'time': t, 'intensity': intensity,
                                                       'product': 'SRI', 'sampleMinutes': 5})
                    except Exception as e:
                        errors.append(f'Intensità SRI assente: {iso(t)} ({type(e).__name__})')
        except Exception as e:
            errors.append(f'Servizio DPC non raggiungibile ({type(e).__name__})')
    if not args.offline and grid is not None:
        update_model(s, root, end, errors, grid)
        update_aspect(s, root, grid, errors)
    if grid is None:
        raise SystemExit('Nessuna griglia radar disponibile: raccolta non avviata')
    payload = publish(root, grid, end, errors)
    print(f"Nuove ore: {saved}; ore presenti: {payload['availableHours']}/336", flush=True)
    for err in errors:
        print(err, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(f"## Radar Sassello\nOre archiviate nella finestra: {payload['availableHours']}/336.\n")
            f.write(f"Ultimo dato: {iso(payload['latestRainTime']) if payload['latestRainTime'] else 'assente'}.\n")
    if not records_fresh(payload, end):
        raise SystemExit('Ultima pioggia misurata assente o più vecchia di 3 ore; archivio preservato')


def records_fresh(payload, end):
    t = payload['latestRainTime']
    return t is not None and end-t <= 3*HOUR


if __name__ == '__main__':
    main()
