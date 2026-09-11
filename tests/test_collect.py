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


if __name__ == '__main__': unittest.main()
