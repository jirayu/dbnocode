#!/usr/bin/env python3
"""
showcase.py - Demo script for dbnocode DSL enhancements
Runs a DSL script demonstrating:
- Extended field types (time, timestamp, uuid, json, email, phone, currency, percentage, color)
- Enhanced lookup (INCLUDE, EXCLUDE, aliasing, multiple sources, cascade)
- Formula fields with formatting
"""

import subprocess
import sys
import tempfile
import os

# DSL script showcasing the features
DSL_CONTENT = '''
APP "Event Showcase" VERSION "1.0"
DATASOURCE "event_db" ADAPTER "sqlite"

FIELDSET address_info
  street_line1 | 40 |
  street_line2 | 40 |
  city         | 30 |
  state_code   | 10 | lookup:state INCLUDE state_code,state_name
  postal_code  | 15 |
  country_code | 10 | lookup:country INCLUDE country_code,country_name
END FIELDSET

FORM country TITLE "Country Master" COLS 2
  country_code | 10 | req prefix:CTR
  country_name | 40 | req span:2
  iso_code     |  4 |
  currency     | 10 | opts:USD,EUR,GBP,JPY,CAD,AUD
  phone_code   | 10 | default:+1
  LIST country_code:10 country_name:30 iso_code:4 currency:10 phone_code:10
END

FORM state TITLE "State/Province Master" COLS 2
  state_code   | 10 | req prefix:ST
  state_name   | 30 | req span:2
  country_code | 10 | req lookup:country INCLUDE country_code,country_name
  LIST state_code:10 state_name:30 country_code:10
END

FORM customer TITLE "Customer Management" COLS 2 TABBED
  customer_id  | 12 | req prefix:CUS
  company_name | 40 | req span:2
  contact_name | 30 |
  email        | 30 | email
  phone        | 20 | phone
  USE address_info
  DETAIL "Contacts" AS customer_contact
    contact_name | 30 | req
    relation     | 20 | opts:Primary,Secondary,Emergency
    phone        | 20 | phone
    email        | 30 | email
  END DETAIL
  LIST customer_id:12 company_name:30 email:30 phone:20
END

FORM event_ticket TITLE "Event Ticket" COLS 2
  ticket_id    | 12 | req prefix:TKT
  event_id     | 12 | req lookup:event
  ticket_type  | 20 | opts:General Admission,VIP,Backstage,Early Bird
  quantity     | 10 | num default:1
  unit_price   | 10 | currency
  total        | 12 | currency formula:quantity * unit_price
  formatted_total | 18 | formula:FORMAT(CURRENCY, quantity * unit_price)
  purchase_date| 12 | date
  purchaser_email | 30 | email
  LIST ticket_id:12 event_id:12 ticket_type:20 quantity:10 unit_price:10 total:12 formatted_total:18
END

FORM event TITLE "Event Management" COLS 2 TABBED
  event_id     | 12 | req prefix:EVT
  event_name   | 50 | req span:2
  start_time   |  8 | time
  end_time     |  8 | time
  event_date   | 12 | date
  created_at   | 19 | timestamp
  organizer_uuid | 36 | uuid
  config_data  | 50 | json
  contact_email| 30 | email
  contact_phone| 20 | phone
  base_price   | 10 | currency
  tax_rate     |  6 | percentage default:0.08
  venue_color  |  7 | color
  venue        | 40 | lookup:venue
  city         | 30 | lookup:city CASCADE state->state_code
  state        | 20 | lookup:state
  country      | 20 | lookup:country
  LIST event_id:12 event_name:30 start_time:8 end_time:8 event_date:12 base_price:10 tax_rate:6
END

FORM venue TITLE "Venue Master" COLS 2
  venue_id     | 12 | req prefix:VEN
  venue_name   | 40 | req span:2
  address      | 60 | span:2 rows:3
  capacity     | 10 | num
  LIST venue_id:12 venue_name:30 address:40 capacity:10
END

FORM city TITLE "City Master" COLS 2
  city_id      | 12 | req prefix:CTY
  city_name    | 30 | req
  state_code   | 10 | req lookup:state
  LIST city_id:12 city_name:30 state_code:10
END

MENU "Main" PULLDOWN
  GROUP "Master Data"
    "Countries"   => country       HOTKEY "F2"
    "States"      => state         HOTKEY "F3"
    "Cities"      => city          HOTKEY "F4"
    "Venues"      => venue         HOTKEY "F5"
    "Customers"   => customer      HOTKEY "F6"
    "---"
    "Exit"        => EXIT          HOTKEY "ESC"
  END GROUP
  GROUP "Transactions"
    "Events"      => event         HOTKEY "F7"
    "Tickets"     => event_ticket  HOTKEY "F8"
  END GROUP
END
'''

def main():
    # Create a temporary file for the DSL script
    with tempfile.NamedTemporaryFile(mode='w', suffix='.dsl', delete=False, encoding='utf-8') as f:
        f.write(DSL_CONTENT)
        dsl_file_path = f.name

    # Use a dedicated showcase DB so company data isn't mixed in
    showcase_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "showcase.db")

    try:
        # Run the dbnocode main script with our DSL file
        print(f"Running DSL showcase from: {dsl_file_path}")
        print(f"Database: {showcase_db}")
        print("Close the TUI window to exit the demo.")
        result = subprocess.run(
            [sys.executable, 'main.py', dsl_file_path, '--db', showcase_db],
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(f"Demo exited with code: {result.returncode}")
    except subprocess.CalledProcessError as e:
        print(f"Error running demo: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: main.py not found. Make sure you're in the dbnocode directory.")
        sys.exit(1)
    finally:
        # Clean up the temporary file
        try:
            os.unlink(dsl_file_path)
            print(f"Cleaned up temporary file: {dsl_file_path}")
        except OSError:
            pass

if __name__ == '__main__':
    main()