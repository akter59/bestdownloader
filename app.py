import os
import re
import time
import shutil
import threading
import uuid
from flask import Flask, render_template, request, jsonify, send_file, after_this_request
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app, expose_headers=["Content-Disposition"])
application = app  # Standard WSGI entry point for Hostinger Passenger & Gunicorn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Check if ffmpeg is available (check imageio_ffmpeg first, then system PATH)
FFMPEG_PATH = None

def init_ffmpeg():
    global FFMPEG_PATH
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        try:
            print("  ⚙️ FFmpeg মডিউল (imageio-ffmpeg) স্বয়ংক্রিয়ভাবে ইনস্টল করা হচ্ছে...")
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "imageio-ffmpeg"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:
            print(f"  ⚠️ imageio-ffmpeg ইনস্টল করা যায়নি: {e}")
            return None

FFMPEG_PATH = init_ffmpeg() or shutil.which('ffmpeg')
HAS_FFMPEG = FFMPEG_PATH is not None

# Node.js runtime detection for YouTube JS challenge solver (ejs)
def find_node():
    n = shutil.which('node') or shutil.which('nodejs')
    if n:
        return n
    possible_paths = [
        # Linux / Hostinger / Cloud server paths
        '/usr/bin/node',
        '/usr/local/bin/node',
        '/bin/node',
        '/usr/bin/nodejs',
        '/usr/local/bin/nodejs',
        # Windows local paths
        os.path.expanduser('~\\AppData\\Local\\hermes\\node\\node.exe'),
        'C:\\Program Files\\nodejs\\node.exe',
        'C:\\Program Files (x86)\\nodejs\\node.exe',
    ]
    for p in possible_paths:
        if os.path.exists(p):
            return p
    return None

NODE_PATH = find_node()

def clean_old_files():
    """Periodically remove files older than 20 minutes from the downloads directory."""
    while True:
        try:
            now = time.time()
            for filename in os.listdir(DOWNLOAD_DIR):
                file_path = os.path.join(DOWNLOAD_DIR, filename)
                if os.path.isfile(file_path):
                    # 20 minutes = 1200 seconds
                    if now - os.path.getmtime(file_path) > 1200:
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(300)

# Start cleanup thread in daemon mode
cleanup_thread = threading.Thread(target=clean_old_files, daemon=True)
cleanup_thread.start()

def sanitize_filename(name):
    """Sanitize filename for safe downloads on all operating systems."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def format_duration(seconds):
    """Format seconds into HH:MM:SS or MM:SS."""
    if not seconds:
        return "N/A"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def format_size(bytes_val):
    """Convert bytes to human-readable string."""
    if not bytes_val or bytes_val <= 0:
        return None
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"

def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'remote_components': ['ejs:github'],
    }
    if NODE_PATH and os.path.exists(NODE_PATH):
        opts['js_runtimes'] = {'node': {'path': NODE_PATH}}
    if FFMPEG_PATH:
        opts['ffmpeg_location'] = FFMPEG_PATH
    return opts

@app.route('/')
def index():
    return render_template('index.html', has_ffmpeg=HAS_FFMPEG)

@app.route('/api/info', methods=['POST'])
def get_video_info():
    data = request.get_json() or {}
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'ভিডিওর লিঙ্ক (URL) প্রদান করুন। (Please provide a video URL)'}), 400

    ydl_opts = get_base_ydl_opts()
    ydl_opts['skip_download'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        err_msg = str(e)
        if "403" in err_msg or "Forbidden" in err_msg:
            try:
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['tv', 'mweb', 'ios', 'android']}}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as e2:
                return jsonify({'error': 'ইউটিউব দ্বারা অ্যাক্সেস সাময়িক ব্লক (403 Forbidden) হয়েছে। লাইব্রেরি আপডেট করতে "pip install -U yt-dlp" কমান্ড দিয়ে পুনরায় চালু করুন।'}), 400
        elif "Private video" in err_msg or "login" in err_msg.lower():
            return jsonify({'error': 'এই ভিডিওটি প্রাইভেট অথবা লগইন প্রয়োজন।'}), 400
        elif "not a valid URL" in err_msg:
            return jsonify({'error': 'সঠিক ভিডিও লিঙ্ক প্রদান করুন।'}), 400
        else:
            return jsonify({'error': f'ভিডিও তথ্য সংগ্রহ করা সম্ভব হয়নি: {err_msg}'}), 400

    title = info.get('title', 'Unknown Title')
    thumbnail = info.get('thumbnail') or (info.get('thumbnails')[-1]['url'] if info.get('thumbnails') else None)
    duration = format_duration(info.get('duration'))
    uploader = info.get('uploader') or info.get('channel') or 'Unknown'
    extractor = info.get('extractor_key', 'Video')

    # Available format options
    raw_formats = info.get('formats', [])
    available_heights = set()
    for f in raw_formats:
        h = f.get('height')
        if h is not None:
            try:
                available_heights.add(int(h))
            except (ValueError, TypeError):
                pass
        res = f.get('resolution') or ''
        if 'x' in res:
            try:
                available_heights.add(int(res.split('x')[1]))
            except Exception:
                pass

    options = []

    # 4K (2160p) - if available
    if any(h >= 2000 for h in available_heights):
        options.append({
            'id': 'video_2160',
            'type': 'video',
            'quality': '2160p (4K Ultra HD)',
            'format': 'MP4',
            'height': 2160,
            'target': '2160'
        })

    # 2K (1440p) - if available
    if any(h >= 1400 for h in available_heights):
        options.append({
            'id': 'video_1440',
            'type': 'video',
            'quality': '1440p (2K Quad HD)',
            'format': 'MP4',
            'height': 1440,
            'target': '1440'
        })

    # 1080p (Full HD) - Always included
    options.append({
        'id': 'video_1080',
        'type': 'video',
        'quality': '1080p (Full HD)',
        'format': 'MP4',
        'height': 1080,
        'target': '1080'
    })

    # 720p (HD) - Always included
    options.append({
        'id': 'video_720',
        'type': 'video',
        'quality': '720p (HD)',
        'format': 'MP4',
        'height': 720,
        'target': '720'
    })

    # 480p (SD)
    options.append({
        'id': 'video_480',
        'type': 'video',
        'quality': '480p (SD)',
        'format': 'MP4',
        'height': 480,
        'target': '480'
    })

    # 360p (Low)
    options.append({
        'id': 'video_360',
        'type': 'video',
        'quality': '360p (Low)',
        'format': 'MP4',
        'height': 360,
        'target': '360'
    })

    # Always add audio option
    options.append({
        'id': 'audio_best',
        'type': 'audio',
        'quality': 'Best Audio (সেরা অডিও)',
        'format': 'MP3',
        'height': None,
        'target': 'audio'
    })

    return jsonify({
        'title': title,
        'thumbnail': thumbnail,
        'duration': duration,
        'uploader': uploader,
        'platform': extractor,
        'options': options,
        'url': url
    })

@app.route('/api/download', methods=['GET'])
def download_video():
    url = request.args.get('url', '').strip()
    download_type = request.args.get('type', 'video')
    target_quality = request.args.get('quality', '720')

    if not url:
        return "ভিডিও লিঙ্ক পাওয়া যায়নি (URL is required)", 400

    unique_id = uuid.uuid4().hex[:10]
    output_template = os.path.join(DOWNLOAD_DIR, f"{unique_id}_%(title)s.%(ext)s")

    ydl_opts = get_base_ydl_opts()
    ydl_opts['outtmpl'] = output_template

    url_lower = url.lower()
    is_facebook = any(d in url_lower for d in ['facebook.com', 'fb.watch', 'fb.com'])

    if download_type == 'audio':
        if HAS_FFMPEG:
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            # Fallback if ffmpeg is missing: grab m4a or best available audio
            ydl_opts.update({
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
            })
    else:
        # Video download
        try:
            h = int(target_quality)
        except ValueError:
            h = 720

        if is_facebook:
            if h >= 720:
                # Facebook HD (1080p / 720p)
                ydl_opts.update({
                    'format': 'hd/bestvideo+bestaudio/best',
                })
            else:
                # Facebook SD (480p / 360p)
                ydl_opts.update({
                    'format': 'sd/best[height<=480]/best',
                })
        else:
            # YouTube & other platforms
            if HAS_FFMPEG:
                # Best video matching requested height + best audio merged into mp4
                ydl_opts.update({
                    'format': f'bestvideo[height<={h}]+bestaudio/best[height<={h}]/best',
                    'merge_output_format': 'mp4'
                })
            else:
                # When ffmpeg is missing, YouTube only allows progressive format
                ydl_opts.update({
                    'format': f'best[height<={h}]/best'
                })

    try:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as first_err:
            err_str = str(first_err)
            if "403" in err_str or "Forbidden" in err_str:
                # 403 Forbidden fallback: retry without downgrading quality
                fallback_opts = dict(ydl_opts)
                fallback_opts['extractor_args'] = {
                    'youtube': {
                        'player_client': ['tv', 'mweb', 'android', 'ios']
                    }
                }
                if download_type == 'audio':
                    fallback_opts['format'] = 'bestaudio/best'
                else:
                    if is_facebook:
                        fallback_opts['format'] = 'hd/best' if h >= 720 else 'sd/best'
                    else:
                        if HAS_FFMPEG:
                            fallback_opts['format'] = f'bestvideo[height<={h}]+bestaudio/best[height<={h}]/best'
                            fallback_opts['merge_output_format'] = 'mp4'
                        else:
                            fallback_opts['format'] = f'best[height<={h}]/best'

                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
            else:
                raise first_err

        format_note = info.get('format_note') or info.get('resolution') or f"{info.get('width')}x{info.get('height')}"
        print(f"  📥 ডাউনলোড সম্পন্ন: '{info.get('title')}' | কোয়ালিটি: {format_note} | ফরম্যাট: {info.get('ext')}")

        # Find the downloaded file
        target_ext = 'mp3' if (download_type == 'audio' and HAS_FFMPEG) else (info.get('ext') or 'mp4')
            
        # Locate file matching unique_id in download dir
        downloaded_file = None
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(unique_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, fname)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            return "ফাইল ডাউনলোড সম্পন্ন করা যায়নি।", 500

        file_title = sanitize_filename(info.get('title', 'download'))
        file_ext = os.path.splitext(downloaded_file)[1]
        download_name = f"{file_title}{file_ext}"

        @after_this_request
        def remove_file(response):
            def delayed_remove():
                time.sleep(10)
                try:
                    if os.path.exists(downloaded_file):
                        os.remove(downloaded_file)
                except Exception:
                    pass
            threading.Thread(target=delayed_remove).start()
            return response

        return send_file(
            downloaded_file,
            as_attachment=True,
            download_name=download_name
        )

    except Exception as e:
        return f"ডাউনলোডে ত্রুটি হয়েছে: {str(e)}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1')
    print("=" * 60)
    print("  🚀 Facebook & YouTube Video Downloader Server Started!")
    print(f"  🌐 সার্ভার চলছে: http://localhost:{port}")
    print(f"  🎬 FFmpeg স্ট্যাটাস: {'ইনস্টল করা আছে ✅' if HAS_FFMPEG else 'পাওয়া যায়নি ⚠️'}")
    print(f"  ⚡ Node.js স্ট্যাটাস: {'পাওয়া গেছে (1080p চ্যালেঞ্জ সলভার সক্রিয়) ✅' if NODE_PATH else 'পাওয়া যায়নি ⚠️'}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=debug)
