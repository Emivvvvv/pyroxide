# -*- coding: utf-8 -*-
from odoo import models, fields
from pyroxide import task
import pyarrow as pa


# Pyroxide task definition.
# The Rust backend receives this buffer via memoryview and processes it completely GIL-free.
@task
def process_financial_data(arrow_buffer: memoryview) -> memoryview:
    """
    Simulated CPU-bound processing task.
    In the Rust engine, the payload is extracted as a Vec<u8> (zero-copy GIL-detached pass),
    processed on background threads, and then signaled.
    """
    pass


class HeavyModel(models.Model):
    _name = "pyroxide.heavy_model"
    _description = "Pyroxide Enterprise ORM Offloading Demo"

    name = fields.Char(string="Task Name", required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        default="draft",
        string="Status",
    )

    # Store task ID from Pyroxide
    pyroxide_task_id = fields.Integer(string="Pyroxide Task ID")
    result_data = fields.Binary(string="Processed Results")

    def action_process_async(self):
        """
        Dispatches a massive ORM batch payload to Pyroxide asynchronously
        and returns control back to the Odoo web loop immediately.
        """
        for record in self:
            record.state = "processing"

            # Serialize model/record data to zero-copy Arrow byte buffer (simulated)
            # In production: pa.Table.from_pylist(record.read()).to_batches()
            raw_data = pa.allocate_buffer(4096)

            # Submit to Pyroxide (returns immediately with a handle)
            handle = process_financial_data(memoryview(raw_data))

            # Save the task ID so background cron/workers can poll or wait later
            record.pyroxide_task_id = handle.task_id

        return True

    def action_process_sync(self):
        """
        Dispatches to Pyroxide and blocks the Odoo worker thread natively
        using the Condvar wake-up mechanism. Releases the GIL so that
        concurrent Odoo requests on other threads are not blocked.
        """
        for record in self:
            record.state = "processing"

            # Serialize data (simulated 4KB payload)
            raw_data = pa.allocate_buffer(4096)

            # Submit to Pyroxide
            handle = process_financial_data(memoryview(raw_data))
            record.pyroxide_task_id = handle.task_id

            # Blocks natively, releasing the GIL
            final_status = handle.wait(timeout_sec=10.0)

            if final_status == "Completed":
                record.state = "done"
            else:
                record.state = "failed"

        return True
