-- One row per video, with the engagement rate the brief uses.
{{ config(materialized='table', tags=['youtube']) }}

select
    video_id,
    cast(published_at as date) as published_date,
    cast(date_trunc('month', cast(published_at as date)) as date) as month,
    title,
    format,
    cast(duration_seconds as integer) as duration_seconds,
    cast(views as bigint) as views,
    cast(likes as bigint) as likes,
    cast(comments as bigint) as comments,
    cast(likes + comments as double) / nullif(cast(views as double), 0) as engagement_rate
from {{ source('raw', 'youtube_videos') }}
