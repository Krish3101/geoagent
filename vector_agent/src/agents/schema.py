from pydantic import BaseModel, Field, model_validator, field_validator
from typing import List, Optional, Any, Dict

class TaskValidationError(Exception):
    pass

class GeoTask(BaseModel):
    region_name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    data_types: List[str] = Field(default_factory=lambda: ["boundary"])
    
    bbox: Optional[List[float]] = None
    max_items: int = Field(default=1, ge=1)
    cloud_cover_lt: int = Field(default=20, ge=0, le=100)
    output_formats: List[str] = Field(default_factory=lambda: ["geojson", "shapefile", "geotiff"])
    format_clarified: bool = False
    
    geometry: Optional[Dict[str, Any]] = None
    reference_file: Optional[str] = None

    @field_validator('region_name')
    @classmethod
    def check_region(cls, v: str) -> str:
        if not v or not v.strip():
            raise TaskValidationError("region_name cannot be empty")
        return v

    @model_validator(mode='after')
    def validate_dates_and_types(self) -> 'GeoTask':
        import datetime
        if self.start_date and self.end_date:
            if self.start_date >= self.end_date:
                raise TaskValidationError("start_date must be before end_date")
            
        if not self.data_types:
            self.data_types = ["boundary"]
            
        valid_types = {"boundary", "features"}
        for dt in self.data_types:
            if dt not in valid_types:
                raise TaskValidationError(f"Invalid data_type: {dt}")
                
        return self
