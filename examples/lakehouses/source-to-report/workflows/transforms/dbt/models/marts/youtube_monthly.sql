-- Monthly reach: what the channel published and what it earned.
{{ config(materialized='table', tags=['youtube']) }}

select
    month,
    count(*) as videos,
    sum(views) as views,
    sum(likes) as likes,
    sum(comments) as comments,
    cast(sum(likes + comments) as double) / nullif(cast(sum(views) as double), 0) as engagement_rate
from {{ ref('youtube_videos') }}
group by month
