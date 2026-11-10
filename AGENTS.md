# Project workflow

## Price collection schedule

Owner rule from 2026-11-10: start price collection every night at 00:00 Europe/Moscow, including all registered models, both cities and manufacturer prices. Follow docs/nightly-collection.md. Never bypass or solve CAPTCHA: the user completes it; defer that source, continue independent sources and do not repeat a pending question. Preserve robots.txt, crawl-delay, Retry-After and existing quality rules. Do not run duplicate collectors or silently catch up missed nightly runs during daytime. Deployment remains separate.

After each user-requested change to this project, validate the relevant behavior, rebuild public/index.html, commit the intended files, and push to evgnasa-lgtm/parser. The owner explicitly authorized this workflow on 2026-11-02. Report the real commit URL; never claim publication when push failed. Preserve remote changes; do not force-push. Do not commit credentials, runtime secrets, personal information, raw browser captures or unrelated files. Keep actual observations and city histories; never generate demonstration prices. Server deployment is separate from GitHub publication.
