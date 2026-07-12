import pyroxide

def main():
    print("Testing Pyroxide Boundary...")
    
    # Call the compiled Rust function
    result = pyroxide.ping("System Initialized")
    
    print(f"Rust returned: {result}")

if __name__ == "__main__":
    main()
