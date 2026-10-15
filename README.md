# HarmonyCircle — Community Problems & Help Platform

A beginner-friendly Flask + SQLite website for college and local communities. People can browse posts publicly; they must create an account or log in before posting, joining a campaign, or offering a solution.

## Features

- **Browse without an account:** explore college and neighbourhood needs, search by words, and filter by category or post type.
- **Account registration/login:** full name, unique username, email, password confirmation, and secure password hashes.
- **Two post types:**
  - **Event / campaign:** event date, time and meeting place are required; past event dates are rejected.
  - **Personal problem:** no event date, time or place fields are shown or stored.
- **College and local community scope:** every post identifies whether it is for a college or local community, plus the specific college/community/area.
- **Campaign participation:** logged-in users can join or leave an event; the organiser receives an in-app notification when someone joins.
- **Text and PDF solutions:** logged-in users can write a solution, attach a PDF (max 8 MB), or do both. The post owner receives an in-app notification.
- **Notifications page:** view activity on your posts and open the relevant problem; unread notifications appear in the navigation bar.
- **Personal dashboard:** review your posts, joined campaigns, and solutions you submitted.
- **Problem status:** owners can update their posts to Open, In Progress, or Resolved.
- **More filters:** filter by category, post type, college/local community, and status.
- **Responsive UI:** desktop and mobile layouts.
- **SQLite database:** created automatically when the app starts.

## Run on Windows

1. Install Python 3.10 or newer from https://www.python.org/downloads/ (select “Add Python to PATH”).
2. Extract this ZIP.
3. Open Command Prompt or PowerShell in the extracted `harmonycircle_upgrade` folder.
4. Create and activate a virtual environment (recommended):

   ```powershell
   py -m venv .venv
   .venv\Scripts\activate
   ```

5. Install Flask:

   ```powershell
   pip install -r requirements.txt
   ```

6. Start the site:

   ```powershell
   python app.py
   ```

7. Open http://127.0.0.1:5000 in your browser.

On macOS/Linux, use `python3 -m venv .venv`, `source .venv/bin/activate`, and `python3 app.py`.

## Test the main flow

1. Open the homepage; posts are viewable without login.
2. Click **Join HarmonyCircle** and create an account using an email address and a password of at least 8 characters.
3. Click **Post a problem**. Choose the community type and then **Event / campaign** to see required event date, time and place fields, or **Personal problem** to hide them.
4. Post a sample event and sign up with a second account. The organiser should see a notification.
5. Open a post from another account and submit a text solution and/or a PDF. The post owner should see a notification.
6. Uploaded PDFs can be downloaded only by the post owner and the person who submitted that solution. The app checks both the file extension and PDF signature.
7. Open **My dashboard** to review posts, joined campaigns, and help submitted; update the status of a post you own.

## Project structure

```text
harmonycircle_upgrade/
  app.py
  requirements.txt
  README.md
  templates/
    base.html
    index.html
    auth.html
    new_problem.html
    detail.html
    notifications.html
    dashboard.html
    edit_problem.html
    error.html
  static/
    style.css
    uploads/       # uploaded PDFs are stored here
  harmonycircle.db # generated automatically on first run; not included in ZIP
```

## Important notes before public deployment

This is a working educational starter, not a fully audited production service. Before publishing it on the internet:

- Set a strong random `HARMONYCIRCLE_SECRET` environment variable; do not use the development fallback.
- Use HTTPS and a production WSGI server (for example, Waitress on Windows or Gunicorn on Linux), and configure secure session cookies.
- Add email verification, password reset, abuse reporting/moderation, rate limiting, and appropriate data-retention rules.
- Review uploaded PDFs for malware and consider private object storage. The app restricts extensions and download permissions, but extension checks are not malware scanning.
- Configure backups for the SQLite database and uploads; use a managed database if the service grows.
- The app uses CSRF tokens on forms and password hashing, but should receive a security review before handling sensitive information.

No email/SMS is sent in this version: notifications appear inside the website. This keeps setup simple and makes the feature testable locally.
