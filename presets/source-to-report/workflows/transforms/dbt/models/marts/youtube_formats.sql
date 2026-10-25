-- Format comparison: average reach and engagement per format.
{{ config(materialized='table', tags=['youtube']) }}

select
    format,
    count(*) as videos,
    sum(views) as views,
    cast(avg(views) as double) as avg_views,
    cast(avg(engagement_rate) as double) as avg_engagement
from {{ ref('youtube_videos') }}
group by format
