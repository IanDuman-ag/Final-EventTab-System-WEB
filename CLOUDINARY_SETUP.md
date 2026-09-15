# Cloudinary image storage

1. In the Cloudinary console, select your product environment and open **Settings > API Keys**. You need the cloud name, API key, and API secret.
2. Install the project requirements with `venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Add these values to the existing `.env` file in the project root. Keep the other settings in that file.

   ```dotenv
   USE_CLOUDINARY=True
   CLOUDINARY_CLOUD_NAME=your-cloud-name
   CLOUDINARY_API_KEY=your-api-key
   CLOUDINARY_API_SECRET=your-api-secret
   ```

4. Restart Django. Upload an image using an existing event, team, or candidate form. Verify it displays correctly and appears in the Cloudinary Media Library. Its image URL should start with `https://res.cloudinary.com/`.

The API secret belongs only in the backend environment, never in frontend code or Git. `.env` is already ignored by this repository. On a hosting service, set the same four environment variables in its private configuration.

New images in the project's image upload directories go to Cloudinary. Existing local images and document uploads remain in `assets/`; keep that directory available and backed up. No database migration is required. Existing images are not automatically uploaded to Cloudinary.

Set `USE_CLOUDINARY=False` to store new uploads locally again. Keep the Cloudinary credentials and packages installed so previously uploaded Cloudinary images continue to work.

Configuration reference: [Cloudinary Python SDK](https://cloudinary.com/documentation/django_integration).
