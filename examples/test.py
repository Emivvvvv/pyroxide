from pyroxide import task


@task
def process_data(payload):
    pass  # Managed natively by Rust now!


handle = process_data("Huge enterprise dataset string")
print(f"Task running... Status: {handle.status}")

# Block until finished
final_status = handle.wait()
print(f"Finished with status: {final_status}")
