#!/usr/bin/env python3
"""
Test script to verify miner key extraction from executable filename works correctly.
This simulates the real-world scenario of the service reading miner keys.
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_filename_extraction():
    """Test extracting miner key from executable filename."""
    print("=== Testing Filename-based Miner Key Extraction ===")
    
    # Import the functions we need
    from installer_utils import create_miner_executable
    
    # Create a temporary executable
    with tempfile.NamedTemporaryFile(suffix='.exe', delete=False) as temp_file:
        temp_file.write(b"#!/usr/bin/env python3\nprint('fake executable')")
        temp_exe = temp_file.name
    
    try:
        # Test miner key
        test_miner_key = "ISM-ABC123DEFG456HIJK789LMNOP012QRST"
        
        # Create executable with embedded miner key
        result_path = create_miner_executable(temp_exe, test_miner_key)
        print(f"✅ Created executable: {os.path.basename(result_path)}")
        
        # Now test the extraction using the actual service function
        # We need to simulate sys.executable pointing to our test file
        original_executable = getattr(sys, 'executable', None)
        original_frozen = getattr(sys, 'frozen', False)
        
        # Mock the executable path
        sys.executable = result_path
        sys.frozen = True
        
        try:
            # Import and test the service function
            from miner_online_simple import extract_miner_key_from_executable
            
            extracted_key = extract_miner_key_from_executable()
            
            if extracted_key:
                print(f"✅ Extracted key: {extracted_key}")
                if extracted_key == test_miner_key:
                    print("✅ Key extraction successful - matches expected key!")
                else:
                    print(f"❌ Key mismatch! Expected: {test_miner_key}, Got: {extracted_key}")
            else:
                print("❌ No key extracted from executable filename")
                
        finally:
            # Restore original values
            if original_executable:
                sys.executable = original_executable
            else:
                delattr(sys, 'executable')
            sys.frozen = original_frozen
        
        # Clean up
        os.unlink(result_path)
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up temp file
        if os.path.exists(temp_exe):
            os.unlink(temp_exe)

def test_fallback_to_file():
    """Test fallback to minerkey.txt when filename doesn't contain miner key."""
    print("\n=== Testing Fallback to minerkey.txt ===")
    
    # Create a temp directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a minerkey.txt file
        miner_key_file = os.path.join(temp_dir, "minerkey.txt")
        test_miner_key = "ISM-FALLBACK123456789ABCDEF012QRST"
        
        with open(miner_key_file, "w") as f:
            f.write(test_miner_key)
        
        # Mock the service to use this directory
        original_executable = getattr(sys, 'executable', None)
        original_frozen = getattr(sys, 'frozen', False)
        
        # Create a simple executable name without miner key
        simple_exe = os.path.join(temp_dir, "miner_service.exe")
        with open(simple_exe, "w") as f:
            f.write("fake")
        
        # Mock sys values
        sys.executable = simple_exe
        sys.frozen = True
        
        try:
            from miner_online_simple import read_miner_key
            
            extracted_key = read_miner_key()
            print(f"✅ Extracted key via fallback: {extracted_key}")
            
            if extracted_key == test_miner_key:
                print("✅ Fallback to minerkey.txt successful!")
            else:
                print(f"❌ Fallback failed! Expected: {test_miner_key}, Got: {extracted_key}")
                
        except Exception as e:
            print(f"❌ Fallback test failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Restore original values
            if original_executable:
                sys.executable = original_executable
            else:
                if hasattr(sys, 'executable'):
                    delattr(sys, 'executable')
            sys.frozen = original_frozen

if __name__ == "__main__":
    print("🧪 Testing Miner Key Extraction System\n")
    
    test_filename_extraction()
    test_fallback_to_file()
    
    print("\n✅ All tests completed!")