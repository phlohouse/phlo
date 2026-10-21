-- Generated from the dbt project by phlo-transform; do not edit.
select "ticket_id", count(*) as n from customer360.stg_support_tickets group by "ticket_id" having count(*) > 1
