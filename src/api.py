"""FastAPI application for karaoke generation."""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List
from pathlib import Path
import tempfile
import urllib.request
from datetime import datetime

from src.config import settings
from src.airtable_client import AirtableClient
from src.karaoke_generator import KaraokeGenerator

app = FastAPI(
    title="Karaoke Generator API",
    description="API for generating karaoke videos from music video clips",
    version="1.0.0"
)

# Initialize clients
airtable_client = AirtableClient()
karaoke_generator = KaraokeGenerator()


class KaraokeGenerationRequest(BaseModel):
    """Request model for karaoke generation."""
    record_id: str = Field(..., description="Airtable record ID")
    video_url: Optional[str] = Field(None, description="Video URL (if not in Airtable)")
    srt_content: Optional[str] = Field(None, description="SRT content (if not in Airtable)")


class KaraokeGenerationResponse(BaseModel):
    """Response model for karaoke generation."""
    success: bool
    message: str
    output_path: Optional[str] = None
    record_id: str


class BatchGenerationRequest(BaseModel):
    """Request model for batch generation."""
    genres: Optional[List[str]] = Field(None, description="Filter by genres")
    event_types: Optional[List[str]] = Field(None, description="Filter by event types")
    decades: Optional[List[str]] = Field(None, description="Filter by decades")
    max_records: Optional[int] = Field(None, description="Maximum number of records to process")


class BatchGenerationResponse(BaseModel):
    """Response model for batch generation."""
    success: bool
    message: str
    total_records: int
    processed: int
    failed: int


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Karaoke Generator API",
        "version": "1.0.0",
        "endpoints": {
            "generate": "/generate",
            "batch": "/batch",
            "health": "/health"
        }
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/generate", response_model=KaraokeGenerationResponse)
async def generate_karaoke(request: KaraokeGenerationRequest):
    """
    Generate a karaoke video for a single track.
    
    Args:
        request: Karaoke generation request
        
    Returns:
        Generation response with output path
    """
    try:
        # Get record from Airtable
        record = airtable_client.get_record(request.record_id)
        fields = record.get("fields", {})
        
        # Get video URL
        video_url = request.video_url or airtable_client.get_video_url(record)
        if not video_url:
            raise HTTPException(
                status_code=400,
                detail="No video URL found in request or Airtable record"
            )
        
        # Get SRT content
        srt_content = request.srt_content or airtable_client.get_lyrics_srt(record)
        if not srt_content:
            raise HTTPException(
                status_code=400,
                detail="No SRT content found in request or Airtable record"
            )
        
        # Create output directory
        output_dir = settings.get_output_path()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        track_name = fields.get("Name", request.record_id).replace(" ", "_")
        output_filename = f"{track_name}_{timestamp}_karaoke.mp4"
        output_path = output_dir / output_filename
        
        # Download video to temporary file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_video:
            print(f"Downloading video from {video_url}...")
            urllib.request.urlretrieve(video_url, tmp_video.name)
            video_path = tmp_video.name
        
        # Generate karaoke
        success, result = karaoke_generator.generate_karaoke(
            video_path=video_path,
            srt_content=srt_content,
            output_path=str(output_path)
        )
        
        # Clean up temporary file
        Path(video_path).unlink(missing_ok=True)
        
        if success:
            # Update Airtable record with output path
            airtable_client.update_record(
                request.record_id,
                {"Karaoke Video Path": str(output_path)}
            )
            
            return KaraokeGenerationResponse(
                success=True,
                message="Karaoke video generated successfully",
                output_path=str(output_path),
                record_id=request.record_id
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate karaoke: {result}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating karaoke: {str(e)}"
        )


@app.post("/batch", response_model=BatchGenerationResponse)
async def batch_generate(
    request: BatchGenerationRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate karaoke videos for multiple tracks matching filters.
    
    Args:
        request: Batch generation request with filters
        background_tasks: FastAPI background tasks
        
    Returns:
        Batch generation response
    """
    try:
        # Get records from Airtable
        records = airtable_client.get_records_by_filters(
            genres=request.genres,
            event_types=request.event_types,
            decades=request.decades,
            max_records=request.max_records
        )
        
        if not records:
            return BatchGenerationResponse(
                success=True,
                message="No records found matching filters",
                total_records=0,
                processed=0,
                failed=0
            )
        
        # Process each record
        processed = 0
        failed = 0
        
        for record in records:
            try:
                record_id = record["id"]
                
                # Get video URL and SRT content
                video_url = airtable_client.get_video_url(record)
                srt_content = airtable_client.get_lyrics_srt(record)
                
                if not video_url or not srt_content:
                    print(f"Skipping record {record_id}: missing video or lyrics")
                    failed += 1
                    continue
                
                # Generate karaoke
                fields = record.get("fields", {})
                output_dir = settings.get_output_path()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                track_name = fields.get("Name", record_id).replace(" ", "_")
                output_filename = f"{track_name}_{timestamp}_karaoke.mp4"
                output_path = output_dir / output_filename
                
                # Download video
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_video:
                    urllib.request.urlretrieve(video_url, tmp_video.name)
                    video_path = tmp_video.name
                
                # Generate
                success, result = karaoke_generator.generate_karaoke(
                    video_path=video_path,
                    srt_content=srt_content,
                    output_path=str(output_path)
                )
                
                # Clean up
                Path(video_path).unlink(missing_ok=True)
                
                if success:
                    airtable_client.update_record(
                        record_id,
                        {"Karaoke Video Path": str(output_path)}
                    )
                    processed += 1
                    print(f"Successfully processed record {record_id}")
                else:
                    failed += 1
                    print(f"Failed to process record {record_id}: {result}")
                    
            except Exception as e:
                failed += 1
                print(f"Error processing record: {str(e)}")
        
        return BatchGenerationResponse(
            success=True,
            message=f"Batch generation completed: {processed} successful, {failed} failed",
            total_records=len(records),
            processed=processed,
            failed=failed
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error in batch generation: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port
    )
