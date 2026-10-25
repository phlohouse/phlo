"""Pandera contracts for the YouTube pack's raw table."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series

from source_to_report.youtube import FORMATS


class VideoSchema(pa.DataFrameModel):
    """One video from the channel export."""

    video_id: Series[str] = pa.Field(unique=True, str_matches=r"^vid-\d{3}$")
    published_at: Series[str] = pa.Field(str_matches=r"^\d{4}-\d{2}-\d{2}$")
    title: Series[str] = pa.Field(nullable=False)
    format: Series[str] = pa.Field(isin=list(FORMATS))
    duration_seconds: Series[int] = pa.Field(ge=1, le=4 * 60 * 60)
    views: Series[int] = pa.Field(ge=0)
    likes: Series[int] = pa.Field(ge=0)
    comments: Series[int] = pa.Field(ge=0)

    @pa.dataframe_check
    def reactions_fit_views(cls, data: pa.DataFrame) -> Series[bool]:
        """A video cannot have more likes or comments than views."""
        return (data["likes"] <= data["views"]) & (data["comments"] <= data["views"])
