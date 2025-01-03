# Changelog

## [1.8.0](https://github.com/vicwomg/pikaraoke/compare/1.7.0...1.8.0) (2025-01-03)


### Features

* add more user preferences to info screen ([39fb27b](https://github.com/vicwomg/pikaraoke/commit/39fb27ba844c860ada8dd2b92cb5e0a852f39860))
* add parameter for full buffering approach ([dd9dba6](https://github.com/vicwomg/pikaraoke/commit/dd9dba61ed8d83c3d40c7c0dda24cc11bba832d5))
* add timestamp to splash screen ([#452](https://github.com/vicwomg/pikaraoke/issues/452)) ([405b1f3](https://github.com/vicwomg/pikaraoke/commit/405b1f3984739f7036653659bf15be388727884a))
* allow CLI arg to specify transcode buffer size ([0a6d0bd](https://github.com/vicwomg/pikaraoke/commit/0a6d0bd2ea7fb26c5887737163bcb8678161a12c))
* allow randomized background music from user-specified dir ([a9307f1](https://github.com/vicwomg/pikaraoke/commit/a9307f1d74132905b2ad2d71e26572daf6b254e0))
* background music, score screen, limit queue, stored prefs ([a70d959](https://github.com/vicwomg/pikaraoke/commit/a70d9595f3059c8de4e993070ddf8648ce2fe79f))
* fade volume transition on skip/play/pause [#180](https://github.com/vicwomg/pikaraoke/issues/180) ([2eeb211](https://github.com/vicwomg/pikaraoke/commit/2eeb2110bdda1affce8297e884e15f1cf9bb4555))
* run pikaraoke on a single port ([a523a52](https://github.com/vicwomg/pikaraoke/commit/a523a52ee4e3deb17c14e8f3ffb922d8dcf85c0f))
* support realtime(ish) notifications on splash screen ([823d83d](https://github.com/vicwomg/pikaraoke/commit/823d83d4f408229d7d43cd110e7d9433267959b4))


### Bug Fixes

* alpha bar not showing on files screen ([25cd3d3](https://github.com/vicwomg/pikaraoke/commit/25cd3d3307cf87603a99b42f394eb09a96019c94))
* audit / clean up translations ([1ca27fc](https://github.com/vicwomg/pikaraoke/commit/1ca27fc59e68662c23b06d81f10810766c05e779))
* change buffer_size to be measured in kb ([3ea97cb](https://github.com/vicwomg/pikaraoke/commit/3ea97cbc172d37b4023b27d1e3f8621dca8fb160))
* chunked stream failures on raspberry pi ([#454](https://github.com/vicwomg/pikaraoke/issues/454)) ([8516b4f](https://github.com/vicwomg/pikaraoke/commit/8516b4fb1e3974e9c63f3ac3e88d60ba7bfb452f))
* improve browser warning message ([7469037](https://github.com/vicwomg/pikaraoke/commit/746903745a919f28c501e86fe065f715c3f97d54))
* improve playback loop by using process exit codes ([45bad39](https://github.com/vicwomg/pikaraoke/commit/45bad39711a616eda0c64de193af4ce6d79d8aa5))
* media file path incorrect on windows ([4a38710](https://github.com/vicwomg/pikaraoke/commit/4a38710ac8a2063637c0e840a2a854437448839b))
* song restarts if now_playing status changes during score ([f4140a5](https://github.com/vicwomg/pikaraoke/commit/f4140a51be7eb4e72c4928f6ea347340a70e3094))
* splash menu iframe doesn't load over https ([677576b](https://github.com/vicwomg/pikaraoke/commit/677576b669777bbc74c0d9631a5ff265b8bf8acf))
* support concurrent splash screens [#448](https://github.com/vicwomg/pikaraoke/issues/448) ([#451](https://github.com/vicwomg/pikaraoke/issues/451)) ([5a5d9ea](https://github.com/vicwomg/pikaraoke/commit/5a5d9eac994e660feeda98e38f767c6af631aa78))
* track wont restart while paused ([f716af0](https://github.com/vicwomg/pikaraoke/commit/f716af04806df377b56276f579cf882a17b1c598))


### Performance Improvements

* only transcode files when necessary ([936c3cd](https://github.com/vicwomg/pikaraoke/commit/936c3cd915b7bef76fe33e96cb68aa90a82a1d7d))
* optimize docker build and arg handling ([185f937](https://github.com/vicwomg/pikaraoke/commit/185f9375e2d85daf1e28ea250acffc4c53b62251))


### Documentation

* simplify README, deprecate unused files ([c13ae28](https://github.com/vicwomg/pikaraoke/commit/c13ae28f97d1fafd30c0585bb6bae14836cea6b2))

## [1.7.0](https://github.com/vicwomg/pikaraoke/compare/1.6.1...1.7.0) (2024-12-24)


### Features

* add arm64/amd64 docker builds to release-please ci ([a07265f](https://github.com/vicwomg/pikaraoke/commit/a07265fbece73b49cef95d99dbf54d4a4dd457e3))
* add dockerization support ([f93168f](https://github.com/vicwomg/pikaraoke/commit/f93168fa5413c6cdc20c265934dc05a42c728be2))


### Bug Fixes

* commitlint error ([69cb031](https://github.com/vicwomg/pikaraoke/commit/69cb03170b325a111d4384c150b725f427d23968))
* File was being deleted even if it was in queue ([86dae29](https://github.com/vicwomg/pikaraoke/commit/86dae29b8fb279e5b8a410d22127cf20564359dd))
* Notification not being shown using Flask Flash ([497196a](https://github.com/vicwomg/pikaraoke/commit/497196a78285831492dd7f166f33513d19c29117))
* Transposing hangs when ffmpeg does not have librubberband ([#435](https://github.com/vicwomg/pikaraoke/issues/435)) ([8939e60](https://github.com/vicwomg/pikaraoke/commit/8939e6030aa967a18baf391c8499757708b0a73e))

## [1.6.1](https://github.com/vicwomg/pikaraoke/compare/1.6.0...1.6.1) (2024-12-02)


### Bug Fixes

* prevent hangs when volume change from exceeds max ([#429](https://github.com/vicwomg/pikaraoke/issues/429)) ([d097311](https://github.com/vicwomg/pikaraoke/commit/d0973114be53759f88a59d33112efffc72ebc6db))

## [1.6.0](https://github.com/vicwomg/pikaraoke/compare/1.5.2...1.6.0) (2024-11-23)


### Features

* Added support to Android via Termux App ([#423](https://github.com/vicwomg/pikaraoke/issues/423)) ([0118733](https://github.com/vicwomg/pikaraoke/commit/0118733d698263bc684829aeed69b3c589df43e5))

## [1.5.2](https://github.com/vicwomg/pikaraoke/compare/1.5.1...1.5.2) (2024-11-05)


### Bug Fixes

* admin passwords not working [#401](https://github.com/vicwomg/pikaraoke/issues/401) ([35550dc](https://github.com/vicwomg/pikaraoke/commit/35550dc858864aa928d5f25f75b57472826a11c0))
* broken upgrade path for --break-system-packages users ([9f01f23](https://github.com/vicwomg/pikaraoke/commit/9f01f23ebaabee2aa72b674ebe668d3247be571d))
* broken upgrade path for --break-system-packages users ([#395](https://github.com/vicwomg/pikaraoke/issues/395)) ([74c9dca](https://github.com/vicwomg/pikaraoke/commit/74c9dcaaf2d3a43bf93d4b179c2809be906855b5))
* Italian translation ([#415](https://github.com/vicwomg/pikaraoke/issues/415)) ([2c791c7](https://github.com/vicwomg/pikaraoke/commit/2c791c7f48129f84c9cf45dc2d857ad6742e4c0e))
* support orangepi devices as raspberry pi w/o hw accel ([658d381](https://github.com/vicwomg/pikaraoke/commit/658d381a82b0c87a321ab4f44d6eefea4bfb3bc0))

## [1.5.1](https://github.com/vicwomg/pikaraoke/compare/1.5.0...1.5.1) (2024-09-23)


### Bug Fixes

* 500 error when renaming while queued ([#399](https://github.com/vicwomg/pikaraoke/issues/399)) ([d84fcb3](https://github.com/vicwomg/pikaraoke/commit/d84fcb3ac8974a56e533c2bc3a2c2a58f91baee2))

## [1.5.0](https://github.com/vicwomg/pikaraoke/compare/1.4.1...1.5.0) (2024-09-04)


### Features

* add messages.mo file for es_VE translation ([3cd7d26](https://github.com/vicwomg/pikaraoke/commit/3cd7d2627f1e3ea2d44b482f896f9a6af750b1af))


### Bug Fixes

* song number overflows to add button when &gt; 999 songs [#379](https://github.com/vicwomg/pikaraoke/issues/379) ([a53c9e0](https://github.com/vicwomg/pikaraoke/commit/a53c9e00e144f5203bbecabe1a02f79dff739b68))


### Documentation

* move TROUBLESHOOTING to wiki ([331e814](https://github.com/vicwomg/pikaraoke/commit/331e814a5299189f5248c8595452af5985da3ef4))
* **readme:** recommend Bookworm OS over bullseye ([15598f2](https://github.com/vicwomg/pikaraoke/commit/15598f22e822e3c2014c63a866ecd3b72530698a))

## [1.4.1](https://github.com/vicwomg/pikaraoke/compare/1.4.0...1.4.1) (2024-09-03)


### Bug Fixes

* **ci:** missing long_description in build ([e5482b0](https://github.com/vicwomg/pikaraoke/commit/e5482b036dee906323aed876bed646237be0df5e))

## [1.4.0](https://github.com/vicwomg/pikaraoke/compare/v1.3.0...1.4.0) (2024-09-03)


### Features

* add a setup script ([2d0e973](https://github.com/vicwomg/pikaraoke/commit/2d0e973717892ec072afb0344a969f471e0400e9))
* create python package ([0c97670](https://github.com/vicwomg/pikaraoke/commit/0c97670bea36eb0f8affa17fd23212f64bbed6a7))
* upgrade yt-dlp on all pikaraoke runs ([fedd6ed](https://github.com/vicwomg/pikaraoke/commit/fedd6ed64e53a1e0fec4bc75368e767b57fa6b7e))


### Bug Fixes

* 287 bug: paths with spaces don't work ([f25e326](https://github.com/vicwomg/pikaraoke/commit/f25e32676066754c69f7bfb5b75b54c84ec7d866))
* modify files minimally to make it run ([168abbb](https://github.com/vicwomg/pikaraoke/commit/168abbb069412c1c2913a222623b0bfa0e5ccdf7))
* python-pygame name for apt installation ([be028ab](https://github.com/vicwomg/pikaraoke/commit/be028ab030f3a5a9e6463d9bcbabf6f957ea0dfe))
* resolve issues with commitlint attempt [#1](https://github.com/vicwomg/pikaraoke/issues/1) ([4c375e6](https://github.com/vicwomg/pikaraoke/commit/4c375e665f2333d527335e16d7db6093d96ed5a5))


### Documentation

* add conventional commits badge ([0c63c7d](https://github.com/vicwomg/pikaraoke/commit/0c63c7db4933bfe4549b19b04dc7a46a342039a6))
* rewrite README ([78df2e8](https://github.com/vicwomg/pikaraoke/commit/78df2e8bfc492c75d3befc09b777fdef8d4855fb))
