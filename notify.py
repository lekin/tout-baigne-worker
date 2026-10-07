#!/usr/bin/env python3
"""
Notification helper for sending WhatsApp messages via Twilio.

Setup:
1. Sign up for Twilio: https://www.twilio.com/try-twilio
2. Enable WhatsApp sandbox: https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn
3. Add credentials to .env file:
   TWILIO_ACCOUNT_SID=your_account_sid
   TWILIO_AUTH_TOKEN=your_auth_token
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886  # Twilio sandbox number
   TWILIO_WHATSAPP_TO=whatsapp:+1234567890     # Your WhatsApp number

Usage:
    from notify import send_whatsapp
    send_whatsapp("Transcription complete!")
"""

import os
from dotenv import load_dotenv

load_dotenv()


def send_whatsapp(message: str) -> bool:
    """
    Send a WhatsApp message via Twilio.
    
    Args:
        message: Message text to send
    
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        from twilio.rest import Client
        
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        from_number = os.getenv('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')
        to_number = os.getenv('TWILIO_WHATSAPP_TO')
        
        if not all([account_sid, auth_token, to_number]):
            print("⚠️  Twilio credentials not configured in .env file")
            return False
        
        client = Client(account_sid, auth_token)
        
        message = client.messages.create(
            body=message,
            from_=from_number,
            to=to_number
        )
        
        print(f"✅ WhatsApp message sent: {message.sid}")
        return True
        
    except ImportError:
        print("⚠️  Twilio not installed. Run: pip install twilio")
        return False
    except Exception as e:
        print(f"❌ Failed to send WhatsApp message: {e}")
        return False


def send_notification(message: str):
    """
    Send notification via available methods.
    Falls back to macOS notification if WhatsApp fails.
    
    Args:
        message: Message text to send
    """
    # Try WhatsApp first
    if send_whatsapp(message):
        return
    
    # Fallback to macOS notification
    try:
        os.system(f'osascript -e \'display notification "{message}" with title "Whisper Testing"\'')
        print(f"✅ macOS notification sent")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
        send_notification(message)
    else:
        print("Usage: python notify.py <message>")
