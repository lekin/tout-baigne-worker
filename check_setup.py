#!/usr/bin/env python3
"""
Diagnostic script to verify the WhisperX hallucination fix setup.
"""

import sys
import importlib.util

def check_module(module_name, package_name=None):
    """Check if a module is installed."""
    package_name = package_name or module_name
    spec = importlib.util.find_spec(module_name)
    if spec is not None:
        print(f"✅ {package_name} is installed")
        return True
    else:
        print(f"❌ {package_name} is NOT installed")
        print(f"   Install with: .venv/bin/pip install {package_name}")
        return False

def check_code_fix():
    """Check if the code fix is in place."""
    try:
        with open('src/karaoke_generator.py', 'r') as f:
            content = f.read()
        
        # Check for the new mapping approach
        if 'original_segments_map' in content:
            print("✅ Code fix is in place (timing-based mapping)")
            
            # Check for debug logs
            if 'DEBUG - First 3 faster-whisper segments' in content:
                print("✅ Debug logging is enabled")
            else:
                print("⚠️  Debug logging might be missing")
            
            # Check for overlap calculation
            if 'overlap_start = max(wx_start, orig_start)' in content:
                print("✅ Overlap calculation is implemented")
            else:
                print("⚠️  Overlap calculation might be missing")
            
            return True
        else:
            print("❌ Code fix is NOT in place (still using index-based mapping)")
            print("   The fix should use 'original_segments_map' instead of 'original_texts'")
            return False
    except FileNotFoundError:
        print("❌ src/karaoke_generator.py not found")
        return False

def main():
    print("=" * 70)
    print("WhisperX Hallucination Fix - Setup Diagnostic")
    print("=" * 70)
    print()
    
    all_ok = True
    
    # Check Python modules
    print("📦 Checking Python modules...")
    all_ok &= check_module('faster_whisper', 'faster-whisper')
    all_ok &= check_module('whisperx', 'whisperx')
    all_ok &= check_module('torch', 'torch')
    all_ok &= check_module('torchaudio', 'torchaudio')
    print()
    
    # Check code fix
    print("🔧 Checking code fix...")
    all_ok &= check_code_fix()
    print()
    
    # Check test file
    print("🧪 Checking test files...")
    try:
        with open('test_whisperx_fix.py', 'r') as f:
            print("✅ test_whisperx_fix.py exists")
    except FileNotFoundError:
        print("⚠️  test_whisperx_fix.py not found (optional)")
    print()
    
    # Summary
    print("=" * 70)
    if all_ok:
        print("✅ All checks passed! Ready to test.")
        print()
        print("Next steps:")
        print("1. Run unit test: python3 test_whisperx_fix.py")
        print("2. Run real test: .venv/bin/python3 -m src.cli generate --record-id recz9D9EVKBB4MzDW --use-genius --fast")
    else:
        print("❌ Some checks failed. Please fix the issues above.")
        sys.exit(1)
    print("=" * 70)

if __name__ == "__main__":
    main()
