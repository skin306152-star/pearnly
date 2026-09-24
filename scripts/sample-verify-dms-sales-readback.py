"""Compatibility entry for the consolidated zero-IO menu-5 snapshot suite.

The shell wrapper copies the companion suite beside this file. No external DMS,
LINE, OCR or Site mutation is performed by the suite; it uses real App imports.
"""
def main():
    import runpy
    import types
    import unittest
    from pathlib import Path

    module = types.ModuleType("dms_query_snapshot_regression")
    module.__dict__.update(runpy.run_path(str(Path(__file__).with_name("test-dms-query-snapshots.py"))))
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("合计: 通过 %d / 失败 %d" % (result.testsRun - len(result.failures) - len(result.errors),
                                   len(result.failures) + len(result.errors)))
    print("RESULT: %s" % ("ALL PASS" if result.wasSuccessful() else "FAIL"))


if __name__ == "__main__":
    main()
