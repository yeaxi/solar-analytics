# Rollback — candidate_20260803T130848Z

Candidate archive: `/Users/rdudka/solar_analytics/reports/candidate_20260803T130848Z/solar_analytics_v2_candidate.tar.gz`
Candidate SHA-256: `a9cdfe0992eed4d1021a0e0d3cf505d8cf65d53a784beaaea5a57626ee99de50`

Pre-deploy backups already created on live HA:
- `/config/backups/solar_analytics_v2_predeploy_20260803T115126Z`
- `/config/backups/solar_analytics_v2_compat_fix_predeploy_20260803T120428Z`

Rollback is permitted only after a failed `ha core check`, new integration exception, or unrelated configuration change. Restore the component/package bytes from the timestamped pre-deploy backup, run `ha core check`, and use one separately approved controlled restart. Do not remove legacy REST consumers as part of rollback.
