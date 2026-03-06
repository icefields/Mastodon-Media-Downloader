#!/usr/bin/env python3
"""
Download all media from a Mastodon account anonymously.
Uses public API - no authentication required for public accounts.

Usage:
    python3 download_mastodon_media.py <mastodon_url> [--output-dir DIR]

Example:
    python3 download_mastodon_media.py https://mastodon.social/@username --output-dir /path/to/save
"""

import argparse
import os
import re
import sys
import time
import json
import requests
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime


class MastodonMediaDownloader:
    """Download media from a Mastodon account using public API."""
    
    def __init__(self, account_url, output_dir="~/Downloads"):
        self.account_url = account_url
        self.output_dir = Path(output_dir)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Accept': 'application/json',
        })
        
        # Parse the URL to get instance and username
        self.instance, self.username, self.account_id = self._parse_url()
        
        # Create output directory for this account
        self.download_dir = self.output_dir / self.username.replace('@', '_at_')
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
        # Track progress
        self.stats = {
            'statuses_checked': 0,
            'media_found': 0,
            'media_downloaded': 0,
            'media_skipped': 0,
            'errors': 0,
        }
    
    def _parse_url(self):
        """Parse Mastodon URL to extract instance, username, and account ID."""
        parsed = urlparse(self.account_url)
        instance = f"{parsed.scheme}://{parsed.netloc}"
        
        # Extract username from path (e.g., /@username or /@username@remote.instance)
        path = parsed.path.strip('/')
        if path.startswith('@'):
            path = path[1:]
        
        # Handle remote account format (@user@instance)
        if '@' in path:
            parts = path.split('@')
            username = parts[0]
            if len(parts) > 1:
                # This is a remote account view - get the actual instance
                remote_instance = parts[1]
                # Try to resolve the actual account
                print(f"ℹ️  Remote account detected: @{username}@{remote_instance}")
                return self._resolve_remote_account(remote_instance, username)
        else:
            username = path
        
        # Get account ID from local instance
        account_id = self._get_account_id(instance, username)
        
        return instance, username, account_id
    
    def _resolve_remote_account(self, remote_instance, username):
        """Resolve a remote account to get its ID."""
        # Try WebFinger or direct lookup on remote instance
        remote_url = f"https://{remote_instance}"
        
        # Try to get account ID from remote instance
        account_id = self._get_account_id(remote_url, username)
        
        if account_id:
            return remote_url, username, account_id
        
        # Fallback: try searching via the viewing instance
        print(f"⚠️  Could not resolve remote account directly, trying search...")
        return None, username, None
    
    def _get_account_id(self, instance, username):
        """Get account ID by looking up username on instance."""
        try:
            # Try lookup by username
            url = f"{instance}/api/v1/accounts/lookup?acct={username}"
            r = self.session.get(url, timeout=30)
            if r.status_code == 200:
                data = r.json()
                print(f"✓ Found account: @{data['username']} ({data['display_name']})")
                print(f"  Followers: {data['followers_count']}, Following: {data['following_count']}")
                print(f"  Statuses: {data['statuses_count']}")
                return data['id']
            
            # Try search
            url = f"{instance}/api/v2/search?q={username}&type=accounts"
            r = self.session.get(url, timeout=30)
            if r.status_code == 200:
                data = r.json()
                if data.get('accounts'):
                    for acc in data['accounts']:
                        if acc['username'].lower() == username.lower():
                            return acc['id']
        except Exception as e:
            print(f"✗ Error getting account ID: {e}")
        
        return None
    
    def _download_file(self, url, filepath):
        """Download a file with retry logic."""
        for attempt in range(3):
            try:
                r = self.session.get(url, timeout=60, stream=True)
                if r.status_code == 200:
                    with open(filepath, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                    return True
                elif r.status_code == 429:  # Rate limited
                    wait = int(r.headers.get('Retry-After', 60))
                    print(f"  ⏳ Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  ✗ HTTP {r.status_code}")
                    return False
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    print(f"  ✗ Error: {e}")
                    return False
        return False
    
    def download_media(self, limit=None, only_media=True):
        """Download all media from the account."""
        if not self.account_id:
            print(f"✗ Could not find account ID for @{self.username}")
            print(f"  The account may be private, deleted, or the instance may require authentication.")
            return False
        
        print(f"\n📥 Downloading media from @{self.username}")
        print(f"   Instance: {self.instance}")
        print(f"   Output: {self.download_dir}")
        if limit:
            print(f"   Limit: {limit} statuses")
        print()
        
        # Get statuses with pagination
        url = f"{self.instance}/api/v1/accounts/{self.account_id}/statuses"
        params = {
            'limit': 40,  # Max per request
            'only_media': 'true' if only_media else 'false',
        }
        
        page = 0
        total_media = 0
        
        while url:
            page += 1
            print(f"📄 Fetching page {page}...")
            
            try:
                r = self.session.get(url, params=params if page == 1 else None, timeout=30)
                
                if r.status_code == 429:  # Rate limited
                    wait = int(r.headers.get('Retry-After', 60))
                    print(f"  ⏳ Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                
                if r.status_code != 200:
                    print(f"  ✗ HTTP {r.status_code}: {r.text[:200]}")
                    break
                
                statuses = r.json()
                if not statuses:
                    print("  No more statuses.")
                    break
                
                for status in statuses:
                    self.stats['statuses_checked'] += 1
                    
                    # Check for media attachments
                    media_attachments = status.get('media_attachments', [])
                    
                    if media_attachments:
                        status_date = status.get('created_at', '')[:10]
                        status_id = status.get('id', 'unknown')
                        
                        for i, media in enumerate(media_attachments):
                            self.stats['media_found'] += 1
                            
                            media_type = media.get('type', 'unknown')
                            if media_type not in ('image', 'gifv', 'video', 'audio'):
                                continue
                            
                            # Get the best quality URL
                            media_url = media.get('url') or media.get('remote_url') or media.get('preview_url')
                            if not media_url:
                                continue
                            
                            # Determine file extension
                            original_url = media.get('url', '')
                            ext = '.jpg'
                            if '.' in original_url.split('/')[-1]:
                                ext = '.' + original_url.split('.')[-1].split('?')[0]
                            elif media_type == 'gifv':
                                ext = '.mp4'
                            elif media_type == 'video':
                                ext = '.mp4'
                            elif media_type == 'audio':
                                ext = '.mp3'
                            
                            # Create filename with date and status ID
                            filename = f"{status_date}_{status_id}_{i+1}{ext}"
                            filepath = self.download_dir / filename
                            
                            if filepath.exists():
                                self.stats['media_skipped'] += 1
                                continue
                            
                            print(f"  📥 [{media_type}] {filename}")
                            
                            if self._download_file(media_url, filepath):
                                self.stats['media_downloaded'] += 1
                                total_media += 1
                            else:
                                self.stats['errors'] += 1
                            
                            # Small delay to be polite
                            time.sleep(0.1)
                    
                    if limit and self.stats['statuses_checked'] >= limit:
                        print(f"\n  ✓ Reached limit of {limit} statuses")
                        break
                
                if limit and self.stats['statuses_checked'] >= limit:
                    break
                
                # Get next page from Link header
                url = None
                params = None  # Don't send params for pagination (they're in the URL)
                
                if 'Link' in r.headers:
                    links = r.headers['Link']
                    # Parse Link header for next page
                    for link in links.split(','):
                        if 'rel="next"' in link:
                            url = link.split(';')[0].strip('<> ')
                            break
                
                # Rate limiting - be polite
                time.sleep(0.5)
                
            except Exception as e:
                print(f"  ✗ Error: {e}")
                self.stats['errors'] += 1
                break
        
        # Print summary
        print(f"\n{'='*50}")
        print(f"✓ Download complete!")
        print(f"  Statuses checked: {self.stats['statuses_checked']}")
        print(f"  Media found:      {self.stats['media_found']}")
        print(f"  Downloaded:       {self.stats['media_downloaded']}")
        print(f"  Skipped (exist):  {self.stats['media_skipped']}")
        print(f"  Errors:           {self.stats['errors']}")
        print(f"  Output:           {self.download_dir}")
        print(f"{'='*50}")
        
        return True
    
    def save_progress(self):
        """Save download progress to a JSON file."""
        progress_file = self.download_dir / '.download_progress.json'
        data = {
            'account_url': self.account_url,
            'instance': self.instance,
            'username': self.username,
            'account_id': self.account_id,
            'last_run': datetime.now().isoformat(),
            'stats': self.stats,
        }
        with open(progress_file, 'w') as f:
            json.dump(data, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description='Download all media from a Mastodon account anonymously.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 download_mastodon_media.py https://mastodon.social/@username@instance.social
    python3 download_mastodon_media.py https://pixelfed.social/@username --output-dir ./downloads
        """
    )
    parser.add_argument('url', help='Mastodon account URL')
    parser.add_argument('--output-dir', '-o', default="~/Downloads",
                        help='Output directory for downloaded media (default: ~/Downloads)')
    parser.add_argument('--limit', '-l', type=int, default=None,
                        help='Limit number of statuses to check (default: all)')
    parser.add_argument('--include-non-media', action='store_true',
                        help='Include statuses without media (slower)')
    
    args = parser.parse_args()
    
    print(f"\n{'='*50}")
    print("Mastodon Media Downloader (Anonymous)")
    print(f"{'='*50}\n")
    
    downloader = MastodonMediaDownloader(args.url, args.output_dir)
    success = downloader.download_media(
        limit=args.limit,
        only_media=not args.include_non_media
    )
    
    if success:
        downloader.save_progress()
    
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())

