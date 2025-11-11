# Changelog

## [1.14.0](https://github.com/vicwomg/pikaraoke/compare/1.13.0...1.14.0) (2025-11-11)

### Features

- allow specifying yt-dlp args in CLI [#537](https://github.com/vicwomg/pikaraoke/issues/537) ([09cf7e8](https://github.com/vicwomg/pikaraoke/commit/09cf7e85e29518c06148aba9ceafbc50f449db29))
- persist "non-karaoke" checkbox state [#536](https://github.com/vicwomg/pikaraoke/issues/536) ([b1a2732](https://github.com/vicwomg/pikaraoke/commit/b1a2732fa1b9c0d32a5937d1c9c6158a2a0780a5))
- prefer h264 codec for youtube download ([#535](https://github.com/vicwomg/pikaraoke/issues/535)) ([38f3dc9](https://github.com/vicwomg/pikaraoke/commit/38f3dc96eed6714cae4f935cbed3d3d5379b74b8))
- russian translation from [@avlubimov](https://github.com/avlubimov) ([6ed5e27](https://github.com/vicwomg/pikaraoke/commit/6ed5e2700b9d5231c812f7b8fe2f24b6fdb29d67))

### Bug Fixes

- unidecode breaks search, add pycharm to gitignore ([#540](https://github.com/vicwomg/pikaraoke/issues/540)) ([467c65d](https://github.com/vicwomg/pikaraoke/commit/467c65d67910ecd2828a64b0c3592a6a2c1c2ddf))

## [1.13.0](https://github.com/vicwomg/pikaraoke/compare/1.12.0...1.13.0) (2025-08-02)

### Features

- Upscale CDG using nearest neigbor algorithm [#511](https://github.com/vicwomg/pikaraoke/issues/511) ([4326f66](https://github.com/vicwomg/pikaraoke/commit/4326f6676cdd8c67933058224cb594247c77fe41))

### Bug Fixes

- error launching on pi: iwconfig is not found [#510](https://github.com/vicwomg/pikaraoke/issues/510) ([b329664](https://github.com/vicwomg/pikaraoke/commit/b3296645dea96d9b668527c976ac293b3789ff6f))
- proxy error "not an accepted origin" [#519](https://github.com/vicwomg/pikaraoke/issues/519) ([#523](https://github.com/vicwomg/pikaraoke/issues/523)) ([c7a90cf](https://github.com/vicwomg/pikaraoke/commit/c7a90cf6e49613284217f969f54122bea65abff9))
- settings checkbox show incorrect state when False ([18e5751](https://github.com/vicwomg/pikaraoke/commit/18e5751eebc564f7d5b919a373b3b4e22873c1dd))
- update outdated translation files ([4dc5432](https://github.com/vicwomg/pikaraoke/commit/4dc5432144892e1efd02e8b38c8fbc6afecc17c2))

### Documentation

- update translation files ([3836d17](https://github.com/vicwomg/pikaraoke/commit/3836d17e1878e56ad06dadd63ed9c0d418c4e0f3))

## [1.12.0](https://github.com/vicwomg/pikaraoke/compare/1.11.1...1.12.0) (2025-07-31)

### Features

- update VE spanish translation 2025 ([#518](https://github.com/vicwomg/pikaraoke/issues/518)) ([50c2411](https://github.com/vicwomg/pikaraoke/commit/50c24115555fd8ab65ac9189542a4ff57333ffd5))
- ZH_TW i18n support and language change UI ([#517](https://github.com/vicwomg/pikaraoke/issues/517)) ([271b426](https://github.com/vicwomg/pikaraoke/commit/271b426a60b89150602827da72d0c50d88bfacfb))

### Bug Fixes

- code quality issues ([af29ecf](https://github.com/vicwomg/pikaraoke/commit/af29ecf630940521bb9b6e9c0bc2e2facfbae816))
- relax yml lint requirements ([22b5335](https://github.com/vicwomg/pikaraoke/commit/22b53351da892ef46f0b7f90df17470e39cfd61b))

## [1.11.1](https://github.com/vicwomg/pikaraoke/compare/1.11.0...1.11.1) (2025-04-26)

### Bug Fixes

- fix merge conflict ([d35ee93](https://github.com/vicwomg/pikaraoke/commit/d35ee93ed286392e92c82984def99ad97d703d81))

### Documentation

- Add correct dockerhub URL ([757b65b](https://github.com/vicwomg/pikaraoke/commit/757b65b1c8ec259844e7d3dfe445099eb27dbd0b))
- add french translation ([#505](https://github.com/vicwomg/pikaraoke/issues/505)) ([d55fbdf](https://github.com/vicwomg/pikaraoke/commit/d55fbdf46c13f58120b4a774c5cab2a8f4306b7b))
- add Italian translation ([26dc459](https://github.com/vicwomg/pikaraoke/commit/26dc459bff15fce3020a5f6e9fbb8f51c2bc4fea))
- fix translation errors ([aa8aff7](https://github.com/vicwomg/pikaraoke/commit/aa8aff77320dbafd99defcbc871880209f2cfbb7))

## [1.11.0](https://github.com/vicwomg/pikaraoke/compare/1.10.3...1.11.0) (2025-02-08)

### Features

- add support for adjusting a/v sync ([#489](https://github.com/vicwomg/pikaraoke/issues/489)) ([489d2ef](https://github.com/vicwomg/pikaraoke/commit/489d2ef214b2951bb2373cedca1ee5db3b0277ba))

### Bug Fixes

- high quality video flag state not displaying in info.html ([3bc7719](https://github.com/vicwomg/pikaraoke/commit/3bc7719397902070073256aa9efff7dd989eb1ca))
- installed eventlet python package prevents launch ([99496cd](https://github.com/vicwomg/pikaraoke/commit/99496cdc643530fc45c7e75b5855e31a3d8b80e3))

### Documentation

- PT-BR translation for Pikaraoke ([455c528](https://github.com/vicwomg/pikaraoke/commit/455c528526ebe786d974d62c8249b428d34159b0))

## [1.10.3](https://github.com/vicwomg/pikaraoke/compare/1.10.2...1.10.3) (2025-01-21)

### Bug Fixes

- confirmation override breaks video playback ([a372d52](https://github.com/vicwomg/pikaraoke/commit/a372d520a5b70c079d2c012952e61ed1f5c9da45))

## [1.10.2](https://github.com/vicwomg/pikaraoke/compare/1.10.1...1.10.2) (2025-01-21)

### Bug Fixes

- Add German translation ([70a9557](https://github.com/vicwomg/pikaraoke/commit/70a95576d6e8e881e1a5afd226142a169044304d))

## [1.10.1](https://github.com/vicwomg/pikaraoke/compare/1.10.0...1.10.1) (2025-01-14)

### Bug Fixes

- Docker build not working in CI ([cd3d9ae](https://github.com/vicwomg/pikaraoke/commit/cd3d9ae3ad7b0fa9426744e118a73055869b2670))

### Documentation

- add additional language templates: NO, RU, TH ([a3c447f](https://github.com/vicwomg/pikaraoke/commit/a3c447fec588cf3c1b57eb06e87bf4d5755fae29))
- update readme for clarity and brevity ([2bfa77f](https://github.com/vicwomg/pikaraoke/commit/2bfa77fc0ea8ca4f1d8dbb5a3c8310bc732cd47a))

## [1.10.0](https://github.com/vicwomg/pikaraoke/compare/1.9.0...1.10.0) (2025-01-14)

### Features

- add placeholder translation files for common languages ([30c9d5b](https://github.com/vicwomg/pikaraoke/commit/30c9d5ba791a577ea7333bec48625f71b2245c7c))
- support for specifying yt-dlp proxy ([e276fbf](https://github.com/vicwomg/pikaraoke/commit/e276fbfe1ae3b7495fe7750bdcb15bd948c7eb72))

### Performance Improvements

- use websocket for player communication ([#466](https://github.com/vicwomg/pikaraoke/issues/466)) ([bbd0360](https://github.com/vicwomg/pikaraoke/commit/bbd036073ef456cdb4e213161cdccea51065b7c5))

## [1.9.0](https://github.com/vicwomg/pikaraoke/compare/1.8.0...1.9.0) (2025-01-08)

### Features

- support splash screen background video ([#464](https://github.com/vicwomg/pikaraoke/issues/464)) ([5774208](https://github.com/vicwomg/pikaraoke/commit/5774208b95ff8530f564ae62c52af69eba4e65bf))

### Bug Fixes

- allow mp4 files in create_random_playlist, add max_songs param ([5c2db91](https://github.com/vicwomg/pikaraoke/commit/5c2db915dc2a72dde93fd2c9f922b598a2a7e027))
- missing delayed halt function ([9cf7ab8](https://github.com/vicwomg/pikaraoke/commit/9cf7ab87e9a67d5a042cc2f97f48e9b789f39ba5))
- tmp directory is not writeable in android [#549](https://github.com/vicwomg/pikaraoke/issues/549) ([b06367b](https://github.com/vicwomg/pikaraoke/commit/b06367b69d7a0dace7c9555f46353028bacb4cd1))
- volume_change logging error, remove logging in bg_playlist ([147364c](https://github.com/vicwomg/pikaraoke/commit/147364cb59c48623e25f02f83e24fe1401692f53))

## [1.8.0](https://github.com/vicwomg/pikaraoke/compare/1.7.0...1.8.0) (2025-01-03)

### Features

- add more user preferences to info screen ([39fb27b](https://github.com/vicwomg/pikaraoke/commit/39fb27ba844c860ada8dd2b92cb5e0a852f39860))
- add parameter for full buffering approach ([dd9dba6](https://github.com/vicwomg/pikaraoke/commit/dd9dba61ed8d83c3d40c7c0dda24cc11bba832d5))
- add timestamp to splash screen ([#452](https://github.com/vicwomg/pikaraoke/issues/452)) ([405b1f3](https://github.com/vicwomg/pikaraoke/commit/405b1f3984739f7036653659bf15be388727884a))
- allow CLI arg to specify transcode buffer size ([0a6d0bd](https://github.com/vicwomg/pikaraoke/commit/0a6d0bd2ea7fb26c5887737163bcb8678161a12c))
- allow randomized background music from user-specified dir ([a9307f1](https://github.com/vicwomg/pikaraoke/commit/a9307f1d74132905b2ad2d71e26572daf6b254e0))
- background music, score screen, limit queue, stored prefs ([a70d959](https://github.com/vicwomg/pikaraoke/commit/a70d9595f3059c8de4e993070ddf8648ce2fe79f))
- fade volume transition on skip/play/pause [#180](https://github.com/vicwomg/pikaraoke/issues/180) ([2eeb211](https://github.com/vicwomg/pikaraoke/commit/2eeb2110bdda1affce8297e884e15f1cf9bb4555))
- run pikaraoke on a single port ([a523a52](https://github.com/vicwomg/pikaraoke/commit/a523a52ee4e3deb17c14e8f3ffb922d8dcf85c0f))
- support realtime(ish) notifications on splash screen ([823d83d](https://github.com/vicwomg/pikaraoke/commit/823d83d4f408229d7d43cd110e7d9433267959b4))

### Bug Fixes

- alpha bar not showing on files screen ([25cd3d3](https://github.com/vicwomg/pikaraoke/commit/25cd3d3307cf87603a99b42f394eb09a96019c94))
- audit / clean up translations ([1ca27fc](https://github.com/vicwomg/pikaraoke/commit/1ca27fc59e68662c23b06d81f10810766c05e779))
- change buffer_size to be measured in kb ([3ea97cb](https://github.com/vicwomg/pikaraoke/commit/3ea97cbc172d37b4023b27d1e3f8621dca8fb160))
- chunked stream failures on raspberry pi ([#454](https://github.com/vicwomg/pikaraoke/issues/454)) ([8516b4f](https://github.com/vicwomg/pikaraoke/commit/8516b4fb1e3974e9c63f3ac3e88d60ba7bfb452f))
- improve browser warning message ([7469037](https://github.com/vicwomg/pikaraoke/commit/746903745a919f28c501e86fe065f715c3f97d54))
- improve playback loop by using process exit codes ([45bad39](https://github.com/vicwomg/pikaraoke/commit/45bad39711a616eda0c64de193af4ce6d79d8aa5))
- media file path incorrect on windows ([4a38710](https://github.com/vicwomg/pikaraoke/commit/4a38710ac8a2063637c0e840a2a854437448839b))
- song restarts if now_playing status changes during score ([f4140a5](https://github.com/vicwomg/pikaraoke/commit/f4140a51be7eb4e72c4928f6ea347340a70e3094))
- splash menu iframe doesn't load over https ([677576b](https://github.com/vicwomg/pikaraoke/commit/677576b669777bbc74c0d9631a5ff265b8bf8acf))
- support concurrent splash screens [#448](https://github.com/vicwomg/pikaraoke/issues/448) ([#451](https://github.com/vicwomg/pikaraoke/issues/451)) ([5a5d9ea](https://github.com/vicwomg/pikaraoke/commit/5a5d9eac994e660feeda98e38f767c6af631aa78))
- track wont restart while paused ([f716af0](https://github.com/vicwomg/pikaraoke/commit/f716af04806df377b56276f579cf882a17b1c598))

### Performance Improvements

- only transcode files when necessary ([936c3cd](https://github.com/vicwomg/pikaraoke/commit/936c3cd915b7bef76fe33e96cb68aa90a82a1d7d))
- optimize docker build and arg handling ([185f937](https://github.com/vicwomg/pikaraoke/commit/185f9375e2d85daf1e28ea250acffc4c53b62251))

### Documentation

- simplify README, deprecate unused files ([c13ae28](https://github.com/vicwomg/pikaraoke/commit/c13ae28f97d1fafd30c0585bb6bae14836cea6b2))

## [1.7.0](https://github.com/vicwomg/pikaraoke/compare/1.6.1...1.7.0) (2024-12-24)

### Features

- add arm64/amd64 docker builds to release-please ci ([a07265f](https://github.com/vicwomg/pikaraoke/commit/a07265fbece73b49cef95d99dbf54d4a4dd457e3))
- add dockerization support ([f93168f](https://github.com/vicwomg/pikaraoke/commit/f93168fa5413c6cdc20c265934dc05a42c728be2))

### Bug Fixes

- commitlint error ([69cb031](https://github.com/vicwomg/pikaraoke/commit/69cb03170b325a111d4384c150b725f427d23968))
- File was being deleted even if it was in queue ([86dae29](https://github.com/vicwomg/pikaraoke/commit/86dae29b8fb279e5b8a410d22127cf20564359dd))
- Notification not being shown using Flask Flash ([497196a](https://github.com/vicwomg/pikaraoke/commit/497196a78285831492dd7f166f33513d19c29117))
- Transposing hangs when ffmpeg does not have librubberband ([#435](https://github.com/vicwomg/pikaraoke/issues/435)) ([8939e60](https://github.com/vicwomg/pikaraoke/commit/8939e6030aa967a18baf391c8499757708b0a73e))

## [1.6.1](https://github.com/vicwomg/pikaraoke/compare/1.6.0...1.6.1) (2024-12-02)

### Bug Fixes

- prevent hangs when volume change from exceeds max ([#429](https://github.com/vicwomg/pikaraoke/issues/429)) ([d097311](https://github.com/vicwomg/pikaraoke/commit/d0973114be53759f88a59d33112efffc72ebc6db))

## [1.6.0](https://github.com/vicwomg/pikaraoke/compare/1.5.2...1.6.0) (2024-11-23)

### Features

- Added support to Android via Termux App ([#423](https://github.com/vicwomg/pikaraoke/issues/423)) ([0118733](https://github.com/vicwomg/pikaraoke/commit/0118733d698263bc684829aeed69b3c589df43e5))

## [1.5.2](https://github.com/vicwomg/pikaraoke/compare/1.5.1...1.5.2) (2024-11-05)

### Bug Fixes

- admin passwords not working [#401](https://github.com/vicwomg/pikaraoke/issues/401) ([35550dc](https://github.com/vicwomg/pikaraoke/commit/35550dc858864aa928d5f25f75b57472826a11c0))
- broken upgrade path for --break-system-packages users ([9f01f23](https://github.com/vicwomg/pikaraoke/commit/9f01f23ebaabee2aa72b674ebe668d3247be571d))
- broken upgrade path for --break-system-packages users ([#395](https://github.com/vicwomg/pikaraoke/issues/395)) ([74c9dca](https://github.com/vicwomg/pikaraoke/commit/74c9dcaaf2d3a43bf93d4b179c2809be906855b5))
- Italian translation ([#415](https://github.com/vicwomg/pikaraoke/issues/415)) ([2c791c7](https://github.com/vicwomg/pikaraoke/commit/2c791c7f48129f84c9cf45dc2d857ad6742e4c0e))
- support orangepi devices as raspberry pi w/o hw accel ([658d381](https://github.com/vicwomg/pikaraoke/commit/658d381a82b0c87a321ab4f44d6eefea4bfb3bc0))

## [1.5.1](https://github.com/vicwomg/pikaraoke/compare/1.5.0...1.5.1) (2024-09-23)

### Bug Fixes

- 500 error when renaming while queued ([#399](https://github.com/vicwomg/pikaraoke/issues/399)) ([d84fcb3](https://github.com/vicwomg/pikaraoke/commit/d84fcb3ac8974a56e533c2bc3a2c2a58f91baee2))

## [1.5.0](https://github.com/vicwomg/pikaraoke/compare/1.4.1...1.5.0) (2024-09-04)

### Features

- add messages.mo file for es_VE translation ([3cd7d26](https://github.com/vicwomg/pikaraoke/commit/3cd7d2627f1e3ea2d44b482f896f9a6af750b1af))

### Bug Fixes

- song number overflows to add button when &gt; 999 songs [#379](https://github.com/vicwomg/pikaraoke/issues/379) ([a53c9e0](https://github.com/vicwomg/pikaraoke/commit/a53c9e00e144f5203bbecabe1a02f79dff739b68))

### Documentation

- move TROUBLESHOOTING to wiki ([331e814](https://github.com/vicwomg/pikaraoke/commit/331e814a5299189f5248c8595452af5985da3ef4))
- **readme:** recommend Bookworm OS over bullseye ([15598f2](https://github.com/vicwomg/pikaraoke/commit/15598f22e822e3c2014c63a866ecd3b72530698a))

## [1.4.1](https://github.com/vicwomg/pikaraoke/compare/1.4.0...1.4.1) (2024-09-03)

### Bug Fixes

- **ci:** missing long_description in build ([e5482b0](https://github.com/vicwomg/pikaraoke/commit/e5482b036dee906323aed876bed646237be0df5e))

## [1.4.0](https://github.com/vicwomg/pikaraoke/compare/v1.3.0...1.4.0) (2024-09-03)

### Features

- add a setup script ([2d0e973](https://github.com/vicwomg/pikaraoke/commit/2d0e973717892ec072afb0344a969f471e0400e9))
- create python package ([0c97670](https://github.com/vicwomg/pikaraoke/commit/0c97670bea36eb0f8affa17fd23212f64bbed6a7))
- upgrade yt-dlp on all pikaraoke runs ([fedd6ed](https://github.com/vicwomg/pikaraoke/commit/fedd6ed64e53a1e0fec4bc75368e767b57fa6b7e))

### Bug Fixes

- 287 bug: paths with spaces don't work ([f25e326](https://github.com/vicwomg/pikaraoke/commit/f25e32676066754c69f7bfb5b75b54c84ec7d866))
- modify files minimally to make it run ([168abbb](https://github.com/vicwomg/pikaraoke/commit/168abbb069412c1c2913a222623b0bfa0e5ccdf7))
- python-pygame name for apt installation ([be028ab](https://github.com/vicwomg/pikaraoke/commit/be028ab030f3a5a9e6463d9bcbabf6f957ea0dfe))
- resolve issues with commitlint attempt [#1](https://github.com/vicwomg/pikaraoke/issues/1) ([4c375e6](https://github.com/vicwomg/pikaraoke/commit/4c375e665f2333d527335e16d7db6093d96ed5a5))

### Documentation

- add conventional commits badge ([0c63c7d](https://github.com/vicwomg/pikaraoke/commit/0c63c7db4933bfe4549b19b04dc7a46a342039a6))
- rewrite README ([78df2e8](https://github.com/vicwomg/pikaraoke/commit/78df2e8bfc492c75d3befc09b777fdef8d4855fb))
