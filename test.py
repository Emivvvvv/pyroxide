import pyroxide
task_id = pyroxide.submit_task("Simulated heavy workload data")
print(f"Task successfully allocated in Slab with ID: {task_id}")