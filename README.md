# 9001ft Ladder

A self-hosted ladder over paywalls, cookie walls and clutter. When 12 feet isn't enough, and 13 isn't either.

9001ft Ladder is a fork of [wasi-master/13ft](https://github.com/wasi-master/13ft) (MIT). It keeps everything 13ft does and adds:

- **Reader view.** One click turns any fetched page into a clean, distraction-free article: title, author, date, reading time, body text and images, with light, dark and sepia themes and adjustable text size. Nothing in the article's wording is changed.
- **Working archive.org pages.** When a page comes from the Wayback Machine, its stylesheets, images and scripts now load from the archived copies, so the page keeps its look (upstream 13ft showed these unstyled with broken images).

Please support the writers you read when you can. This is for the one article you want to read and move on.

## How does it work?

It first asks for the page as Googlebot (Google's crawler), since many sites give Google the whole article so it can be indexed. Many large sites now check that Googlebot requests really come from Google, so when that fails it falls back to saved copies: Freedium for Medium, then archive.org, then archive.today. The status page shows each step as it happens. (Multi-source fallbacks courtesy of [@JRS1986](https://github.com/JRS1986).)

Saved copies have limits. archive.today removes all JavaScript from what it saves, so animations and interactive graphics can't come back from there; archive.org keeps them, and 9001ft now loads them.

### Reader view

- Tick **Open in Reader view** on the home page, or
- press the **📖 Reader view** button on any fetched page, or
- go straight to `http://localhost:5013/reader/<article URL>`.

## How do I use it?

### Using Docker

Requirements: Docker with Docker Compose (`docker compose`).

```sh
git clone https://github.com/TheRealEminem/9001ft.git
cd 9001ft
docker compose up --build
```

Then open <http://localhost:5013>. It listens on 127.0.0.1 only: anyone who can reach it can fetch any page through it, so don't expose it publicly without putting it behind authentication. (Port 5013 rather than 5000 because macOS AirPlay Receiver uses 5000.)

### Standard Python script

First, make sure you have [python](https://python.org) installed on your machine. Next, clone the git repo. Then go to a terminal (`Command Prompt` on Windows, `Terminal` on Mac) and run the following command:

From the git cloned directory on your computer:

```sh
cd app/
python -m pip install -r requirements.txt
```

If that doesn't work retry but replace `python` with `py`, then try `python3`, then try `py3`

Then run `portable.py`, click [this link](https://realpython.com/run-python-scripts/) for a tutorial on how to run python scripts.

```sh
python portable.py
```

Or you can also run via gunicorn as follows:

```sh
python -m gunicorn 'portable:app'
```

Then open the link shown in the terminal in the browser and you'll be able to use this script

On a Mac, port 5000 is taken by AirPlay Receiver, and the default binds to every network interface. To run on this machine only, on port 5013, run this from the top folder of the repo (not `app/`):

```sh
python -m gunicorn --chdir app -b 127.0.0.1:5013 --threads 4 portable:app
```

### Installation using venv and running under specific bind address / port

```sh
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
FLASK_APP=app/portable.py flask run --host=127.0.0.1 --port=9982
```

## Using as a Bookmarklet in Chrome:

You can create a bookmarklet that performs the URL transformation by writing a small JavaScript snippet. Below is the JavaScript code for your bookmarklet:

```javascript
javascript: (function () {window.location.href="http://localhost:5013/" + encodeURIComponent(window.location.href);})();
```

You can replace http://localhost:5013/ with your own 9001ft instance if desired.

Steps:

1. Open Bookmarks Manager:

2. Click on the three dots (menu) in the top-right corner of Chrome.
   Go to Bookmarks > Bookmark manager, or simply press Ctrl+Shift+O on Windows/Linux or Cmd+Option+B on Mac.
   Create a New Bookmark:

3. In the Bookmark Manager, click the three-dot menu in the top-right corner of the window and select Add new bookmark.
   Enter Bookmark Details: - Name: Enter a name for your bookmarklet, such as "9001ft-ize". This name will show as a bookmark title in the bookmarks bar - URL: Paste the JavaScript code provided above into the URL field.
4. Click Save.

Using the Bookmarklet:

Navigate to the page whose URL you want to use 9001ft on.

Click on the bookmarklet you saved in your bookmarks bar. The browser will redirect you to the 9001ft version of the URL using your service.

To show Bookmarks in Chrome, click the icon with three horizontal bars in the top right corner to open options. 2. In options, hover over "Bookmarks" to display a second menu where you can click the "Show bookmarks bar" text to toggle the bar on or off.

Instructions courtesy of [@barakplasma](https://github.com/barakplasma)

## Customizing listening host and port, Systemd / Reverse-proxy example

### Systemd Service

```
/lib/systemd/system/9001ft.service
```

```
[Unit]
Description=9001ft Flask Service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
Restart=on-failure
RestartSec=10
User=www-data
Group=www-data
Environment=APP_PATH=/var/www/paywall-break
Environment=FLASK_APP=app/portable.py

ExecStart=/bin/bash -c "cd ${APP_PATH};${APP_PATH}/venv/bin/flask run --host=127.0.0.1 --port=22113"

# Make sure stderr/stdout is captured in the systemd journal.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Reverse Proxy

```
<VirtualHost *:22114>
    ErrorLog ${APACHE_LOG_DIR}/9001ft-error.log
    CustomLog ${APACHE_LOG_DIR}/9001ft-access.log combined

    ProxyRequests Off

    SSLEngine on
    SSLCertificateFile      /etc/ssl/certs/ssl-cert-snakeoil.pem
    SSLCertificateKeyFile /etc/ssl/private/ssl-cert-snakeoil.key
    Header always set Strict-Transport-Security "max-age=63072000"
    SSLProtocol             all -SSLv3 -TLSv1 -TLSv1.1

    SSLHonorCipherOrder     off
    SSLSessionTickets       off

    Protocols h2 http/1.1

    <Proxy *>
        Order deny,allow
        Allow from all
    </Proxy>


    ProxyPass / http://127.0.0.1:22113/
    ProxyPassReverse / http://127.0.0.1:22113/


</VirtualHost>
```

## Localisation

The UI strings can be localised by setting the `LOCALE` environment variable before starting the app. Locale files live in `app/locales/<locale>.json`.

Built-in locales:

- `en` (default)
- `de`
- `fr`
- `ko`

Examples:

```sh
# from app
LOCALE=de python portable.py

# or with gunicorn
LOCALE=fr python -m gunicorn 'portable:app'
```

To add a new locale, copy `app/locales/en.json` to a new file such as `app/locales/es.json` and translate the values.

## Screenshots

### Step 1

![step 1 screenshot](screenshots/step-1.png)
Go to the website at the url shown in the console

### Step 2

![step 2 screenshot](screenshots/step-2.png)
Click on the input box

### Step 3

![step 3 screenshot](screenshots/step-3.png)
Paste your desired url

### Step 4

![step 4 screenshot](screenshots/step-4.gif)
Voilà you now have bypassed the paywall and ads

### Alternative method

You can also append the url at the end of the link and it will also work. (e.g if your server is running at `http://127.0.0.1:5013` then you can go to `http://127.0.0.1:5013/https://example.com` and it will read out the contents of `https://example.com`)

This feature was implemented by [@atcasanova](https://github.com/atcasanova)

### Logo attribution

Logo courtesy of [@mcmikemn](https://github.com/mcmikemn)

## Automated tests

From the repository root, install the application dependencies and run:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The suite uses Python's standard-library `unittest` and Flask's test client.
External HTTP calls are mocked; the tests do not contact article sites or archive services.
Coverage includes routes, error responses, localization, HTML processing, cache expiry,
archive fallbacks, and server-sent progress events. This does not validate live third-party availability.

The GitHub Actions build workflow (`.github/workflows/build.yaml`) runs the suite with pytest on every push
and pull request, before the Docker image is built. A test failure stops the workflow before anything is pushed.
