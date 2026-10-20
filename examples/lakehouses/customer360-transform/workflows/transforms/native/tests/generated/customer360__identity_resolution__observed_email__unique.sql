select "observed_email", count(*) as n from customer360.identity_resolution group by "observed_email" having count(*) > 1
