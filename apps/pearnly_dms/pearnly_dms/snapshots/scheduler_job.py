"""Native scheduler extension for the DMS read-snapshot dispatcher only.

The normal product worker also consumes long writes. Its default queue must not
delay dispatch of the dedicated read lane. Other scheduled jobs retain Frappe's
queue selection, retries, deduplication and execution bookkeeping unchanged.
"""


class DmsSnapshotScheduler:
    def get_queue_name(self):
        if self.method == "pearnly_dms.snapshots.schedule.sweep":
            return "dms_sync"
        return super().get_queue_name()
