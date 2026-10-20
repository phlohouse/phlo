select "observed_email", count(*) as n from customer360.stg_marketing_contacts group by "observed_email" having count(*) > 1
