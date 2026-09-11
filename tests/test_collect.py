import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('collect', Path(__file__).resolve().parents[1]/'scripts/collect.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class RainfallAccounting(unittest.TestCase):
    def setUp(self):
        self.end = 1800000000000//c.HOUR*c.HOUR
        self.grid = {'width': 2, 'height': 1, 'active': [True, True]}

    def test_missing_is_not_dry_and_intervals_sum_once(self):
        records = [{'time':self.end-c.HOUR,'rain':[10,None]},
                   {'time':self.end,'rain':[0,None]}]
        s = c.summarize(records,self.grid,self.end)
        self.assertEqual(s['rainMm'], [10,None])
        self.assertEqual(s['validHours'], [2,0])
        self.assertEqual(s['lastSignificantRain'], [self.end-c.HOUR,None])

    def test_old_rain_excluded_and_weighted_age(self):
        records = [{'time':self.end-240*c.HOUR,'rain':[200,200]},
                   {'time':self.end-216*c.HOUR,'rain':[90,0]},
                   {'time':self.end,'rain':[10,0]}]
        s = c.summarize(records,self.grid,self.end)
        self.assertEqual(s['rainMm'],[100,0])
        self.assertAlmostEqual(s['ageDays'][0],8.12,places=2)
        self.assertIsNone(s['ageDays'][1])
        self.assertEqual(s['validHours'],[2,2])

    def test_duplicate_and_overlapping_windows_rejected(self):
        record = {'time':self.end,'rain':[1,1]}
        with self.assertRaises(ValueError): c.summarize([record,record],self.grid,self.end)
        with self.assertRaises(ValueError): c.summarize([{'time':self.end-300000,'rain':[1,1]}],self.grid,self.end)

    def test_window_boundary_and_shorter_period(self):
        records = [{'time':self.end-24*c.HOUR,'rain':[50,1]}, {'time':self.end-23*c.HOUR,'rain':[2,0]}]
        s=c.summarize(records,self.grid,self.end,24)
        self.assertEqual(s['rainMm'],[2,0])

    def test_archive_roundtrip_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old={'time':self.end-400*c.HOUR,'rain':[99,99]}
            now={'time':self.end,'rain':[0,None]}
            c.write_json(c.sample_path(root,old['time']),old)
            c.write_json(c.sample_path(root,now['time']),now)
            self.assertEqual(c.timeline(root,self.end),[now])
            self.assertTrue(c.sample_path(root,old['time']).exists())
            self.assertEqual(c.read_json(c.sample_path(root,now['time'])),now)


class OptionalLayers(unittest.TestCase):
    def test_terrain_south_is_maximum_and_north_minimum(self):
        from unittest.mock import Mock
        grid = {'width':3,'height':3,'active':[False]*4+[True]+[False]*4,
                'corners': [[44-r*.01,8+col*.01] for r in range(4) for col in range(4)]}
        for elevations, expected in [([100,100,100,50,50,50,0,0,0],1),
                                     ([0,0,0,50,50,50,100,100,100],0)]:
            with tempfile.TemporaryDirectory() as tmp:
                response=Mock(); response.json.return_value={'elevation':[elevations[i] for i in [1,3,4,5,7]]}
                client=Mock(); client.get.return_value=response
                errors=[]; c.update_aspect(client,Path(tmp),grid,errors)
                self.assertEqual(errors,[])
                self.assertEqual(c.read_json(c.aspect_path(Path(tmp)))['score'][4],expected)

    def test_model_returns_spatial_arrays_and_preserves_missing(self):
        from unittest.mock import Mock
        end=1800000000000//c.HOUR*c.HOUR
        grid={'width':1,'height':1,'active':[True],
              'corners':[[44,8],[44,8.01],[43.99,8],[43.99,8.01]]}
        response=Mock(); response.json.return_value={'hourly':{'time':[end//1000], 'shortwave_radiation':[200]}}
        client=Mock();client.get.return_value=response
        with tempfile.TemporaryDirectory() as tmp:
            errors=[];c.update_model(client,Path(tmp),end,errors,grid)
            self.assertEqual(errors,[])
            result=c.model_timeline(Path(tmp),end)
            self.assertEqual(result[0]['solar'],[200])
            self.assertEqual(result[0]['temperature'],[None])


if __name__ == '__main__': unittest.main()
