select "canonical_email", count(*) as n from customer360.consent_current group by "canonical_email" having count(*) > 1
