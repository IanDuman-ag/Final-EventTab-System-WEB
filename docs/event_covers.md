# Dynamic event covers

Current public design: SVG-based covers have been restored. Managed files under
`event_covers/` are ignored by public event serializers; custom uploads are still
shown. `EVENT_COVERS_ENABLED` now defaults to false, so no automatic imports or
generation are active unless explicitly enabled in the backend environment.
Existing cover records/files and the additive tables are retained without deleting
data or reversing the production migration. The implementation below is dormant.

The existing `Event.image` field remains the public cover. An uploaded image always
wins. The title, sport and special event type are matched before the category,
using the curated catalog in `events/cover_catalog.py`.

On a successful event save, an after-commit callback imports a matching bundled
photograph into Django media storage once and associates it with the event.
Other events of that type reuse the same `EventCoverAsset`. If a matching photo
is not available, the type is queued without an API call. A cinematic intramurals
photo is the read-only public fallback until a specific cover is ready.
Changing a title remaps a managed cover to the new type; it never replaces an
admin upload. Successful generation attaches the shared cover to matching events
that still have no uploaded image, including events outside an `--event` filter.

The save callback never calls OpenAI, and public GET requests never write files,
update records or generate images. Bulk imports using `bulk_create` or `update`
do not run Django signals; run the preparation command afterward.

## Setup and operation

Apply the additive migration and import existing covers:

```powershell
python manage.py migrate
python manage.py prepare_event_covers
```

Put `OPENAI_API_KEY` only in the backend environment or private `.env`. See
`.env.example` for the model and limits. The key is never sent to templates or
JavaScript. Generation uses the documented Images API with one landscape photo
per request: <https://developers.openai.com/api/reference/resources/images/methods/generate>.

Generate up to two missing cover types explicitly:

```powershell
python manage.py prepare_event_covers --generate --limit 2
```

Scope preparation to one or more events with `--event 123`. Failed generations
are skipped unless `--retry-failed` is supplied. The default limits are five API
attempts per day, two attempts per cover type, and at most five per command.
Failures count against the budget; HTTP requests are not automatically retried.
Concurrent workers serialize the daily budget and claim each unique cover type
before any network call. An interrupted `generating` record stays locked to
prevent duplicate charges; an operator must check API usage before resetting it.

For automatic processing, schedule the bounded command using the server's
existing scheduler. No scheduler or API spending is enabled by installing this
change. Pending events keep the photo fallback if no worker is scheduled.

Saved photos are 1200x675 WebP, under 300 KB, with metadata removed. They use the
existing default Django storage, including Cloudinary when configured. Ensure
the production `/assets/` route serves the configured `MEDIA_ROOT`, or use the
existing Cloudinary configuration. A separate root-owned static publication
directory does not automatically publish newly generated media. Public fallback
photos are embedded to remain available under the current protected static setup.

## Bundled generated photographs

Four new photographs were generated with the built-in image generation tool and
saved under `frontend/landingpage/assets/competitions/`:

- `photography-cover.webp`: professional camera and printed photographs at a university photo competition.
- `art-cover.webp`: student hands painting a competition poster with paintbrushes and palette.
- `quiz-cover.webp`: quiz bee stage with contestant podiums and yellow buzzers.
- `intramurals-cover.webp`: gold trophy in a university sports arena.

The exact built-in prompt template was:

```text
Generate ONE standalone landscape photographic event cover, 1536x1024 landscape composition. Subject: {subject} Realistic cinematic editorial photography, consistent deep navy blue shadows, warm yellow-gold highlights, clean white accents. Professional natural materials, sophisticated lighting and shallow depth of field. Main subject near center, framed with generous surrounding space to survive a wide 3:1 card crop. NO typography, watermarks, infographic elements, illustration, vector art or collage. This image will be a reusable cover photograph in the EventTab intramurals website.
```

Subject values:

- Photography: `A professional DSLR camera and telephoto lens resting on a navy-draped judges' table beside several printed landscape photographs at a university photography competition, no readable text.`
- Art: `Close-up of a student's hands painting a colorful competition poster at a university art contest, real paintbrushes, palette, charcoal pencils and paper, abstract paint shapes only, no letters.`
- Quiz: `A university quiz bee stage with three unoccupied contestant podiums, small illuminated yellow buzzer buttons, white spotlights and dark navy curtain, no readable text or logos.`
- Intramurals: `A gleaming gold trophy on a navy plinth in a university indoor sports arena, soft silhouettes of a cheering crowd in the distance and white overhead spotlights, no text or logos.`

Original tool outputs were retained; repository files are optimized 16:9 versions.

Existing crown, dance, duet, vocal, basketball and badminton photos are reused.
No paid backend API request is needed to import these ten bundled photographs.

## Verification

```powershell
python execution/deploy_checks.py events.test_event_covers core.test_public_portal core.test_storage
```

This runs against an isolated disposable PostgreSQL database. API calls in the
tests are mocked. Inspect Games & Events at desktop and phone widths; uploaded
covers, event details, pagination and Major/Minor filters should remain intact.
