"""Lalal.ai API integration for vocal separation."""
import requests
import time
import os
from typing import Optional
from src.config import settings


class LalalAIClient:
    """Client for Lalal.ai API vocal separation."""
    
    # Note: The actual Lalal.ai API URL may be different
    # Check https://www.lalal.ai/api/ for the correct endpoints
    BASE_URL = "https://www.lalal.ai/api"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Lalal.ai API client.
        
        Args:
            api_key: Lalal.ai API key (uses config if not provided)
        """
        self.api_key = api_key or settings.lalal_api_key
        if not self.api_key:
            raise ValueError("Lalal.ai API key not configured. Set LALAL_API_KEY in .env file.")
        
        self.headers = {
            'Authorization': f'license {self.api_key}'
        }
    
    def check_balance(self) -> dict:
        """
        Check account balance and available minutes.
        
        Returns:
            Dict with balance information
        """
        try:
            response = requests.get(
                f"https://www.lalal.ai/billing/get-limits/?key={self.api_key}",
                timeout=10
            )
            response.raise_for_status()
            result = response.json()
            
            if result.get('status') == 'error':
                raise ValueError(f"API Error: {result.get('error')}")
            
            return result
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise ValueError("Invalid API key. Please check your LALAL_API_KEY in .env file.")
            raise
    
    def upload_file(self, file_path: str) -> dict:
        """
        Upload audio file for processing.
        
        Args:
            file_path: Path to audio/video file
            
        Returns:
            Dict with upload information including file ID
        """
        filename = os.path.basename(file_path)
        
        with open(file_path, 'rb') as f:
            file_data = f.read()
            
        headers = self.headers.copy()
        headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        response = requests.post(
            f"{self.BASE_URL}/upload/",
            headers=headers,
            data=file_data
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('status') == 'error':
            raise ValueError(f"Upload error: {result.get('error')}")
        
        return result
    
    def split_audio(self, file_id: str, stem_type: str = "vocals") -> dict:
        """
        Start audio splitting task.
        
        Args:
            file_id: File ID from upload
            stem_type: Type of stem to extract
            
        Returns:
            Dict with task information
        """
        import json
        
        params = json.dumps([{
            'id': file_id,
            'stem': stem_type
        }])
        
        response = requests.post(
            f"{self.BASE_URL}/split/",
            headers=self.headers,
            data={'params': params}
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('status') == 'error':
            raise ValueError(f"Split error: {result.get('error')}")
        
        return result
    
    def check_status(self, file_id: str) -> dict:
        """
        Check processing status of a file.
        
        Args:
            file_id: File ID from upload
            
        Returns:
            Dict with status information
        """
        response = requests.post(
            f"{self.BASE_URL}/check/",
            headers=self.headers,
            data={'id': file_id}
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('status') == 'error':
            raise ValueError(f"Check error: {result.get('error')}")
        
        return result
    
    def download_result(self, url: str, output_path: str) -> bool:
        """
        Download processed file from URL.
        
        Args:
            url: Download URL from check_status
            output_path: Where to save the downloaded file
            
        Returns:
            True if successful
        """
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return True
    
    def separate_vocals(
        self,
        input_file: str,
        output_file: str,
        stem_type: str = "vocals",
        wait: bool = True,
        max_wait_seconds: int = 300
    ) -> Optional[str]:
        """
        Complete workflow: upload, wait, download.
        
        Args:
            input_file: Path to input audio/video file
            output_file: Path where to save separated vocals
            stem_type: Type of stem to extract
            wait: If True, wait for processing to complete
            max_wait_seconds: Maximum time to wait for processing
            
        Returns:
            Path to output file if successful, None otherwise
        """
        try:
            print(f"🎵 Uploading to Lalal.ai for vocal separation...")
            
            # Check balance first
            balance = self.check_balance()
            minutes_left = balance.get('process_duration_left', 0)
            print(f"💰 Account balance: {minutes_left:.1f} minutes remaining")
            
            # Upload file
            upload_result = self.upload_file(input_file)
            file_id = upload_result.get('id')
            
            if not file_id:
                print("❌ Failed to get file ID from upload")
                return None
            
            duration = upload_result.get('duration', 0)
            print(f"✓ Uploaded successfully. File ID: {file_id} (Duration: {duration:.1f}s)")
            
            # Start splitting
            print(f"🔪 Starting audio split for stem: {stem_type}...")
            split_result = self.split_audio(file_id, stem_type)
            print(f"✓ Split task started")
            
            if not wait:
                print(f"⏱️  Processing in background. Use file ID {file_id} to check status.")
                return None
            
            # Wait for processing
            print("⏱️  Processing (this may take 1-3 minutes)...")
            start_time = time.time()
            
            while time.time() - start_time < max_wait_seconds:
                status_result = self.check_status(file_id)
                file_status = status_result.get('result', {}).get(file_id, {})
                
                task_info = file_status.get('task', {})
                task_state = task_info.get('state')
                
                if task_state == 'success':
                    print("✓ Processing complete!")
                    break
                elif task_state == 'error':
                    error_msg = task_info.get('error', 'Unknown error')
                    print(f"❌ Processing failed: {error_msg}")
                    return None
                elif task_state == 'progress':
                    progress = task_info.get('progress', 0)
                    elapsed = int(time.time() - start_time)
                    print(f"⏳ Processing... {progress}% ({elapsed}s elapsed)")
                    time.sleep(5)
                else:
                    # Waiting to start or unknown state
                    elapsed = int(time.time() - start_time)
                    print(f"⏳ Waiting... ({elapsed}s elapsed)")
                    time.sleep(5)
            else:
                print(f"⏱️  Timeout after {max_wait_seconds}s")
                return None
            
            # Get download URL
            split_info = file_status.get('split')
            if not split_info:
                print("❌ No split information available")
                return None
            
            download_url = split_info.get('stem_track')
            if not download_url:
                print("❌ No download URL available")
                return None
            
            # Download result
            print(f"📥 Downloading separated vocals...")
            if self.download_result(download_url, output_file):
                file_size = os.path.getsize(output_file)
                print(f"✓ Downloaded: {output_file} ({file_size / (1024*1024):.1f} MB)")
                return output_file
            else:
                print("❌ Download failed")
                return None
                
        except requests.exceptions.HTTPError as e:
            print(f"❌ API Error: {e}")
            if e.response is not None:
                print(f"Response: {e.response.text}")
            return None
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return None
