"""
Config Manager - Handles save/load/export/import of CANtroller configurations
"""
import csv
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from can_manager import CANManager, ResponseRule, TransmitMessage


class ConfigManager:
    """Manages persistence of CANtroller configurations, logs, and CAN databases"""
    
    def __init__(self, can_manager: CANManager):
        self.can_manager = can_manager
        self.id_database: Dict[int, str] = {}       # CAN ID -> name
        self.name_to_id: Dict[str, int] = {}         # block_name -> ID
        self.signal_database: Dict[int, List[dict]] = {}  # CAN ID -> signal defs
    
    # === Configuration Save/Load ===
    
    def save_config(self, filename: str, channel: str, bitrate: str) -> None:
        """Save full configuration to .cantroller file"""
        config = {
            'version': '1.0',
            'settings': {
                'channel': channel,
                'bitrate': bitrate
            },
            'periodic_messages': [],
            'response_rules': []
        }
        
        for msg in self.can_manager.get_transmit_messages():
            config['periodic_messages'].append({
                'msg_id': msg.msg_id,
                'data': msg.data,
                'is_extended': msg.is_extended,
                'cycle_time_ms': msg.cycle_time_ms,
                'increment_byte': msg.increment_byte,
                'is_paused': msg.is_paused,
                'comment': msg.comment
            })
        
        for rule in self.can_manager.get_response_rules():
            config['response_rules'].append({
                'trigger_id': rule.trigger_id,
                'response_id': rule.response_id,
                'response_data': rule.response_data,
                'increment_byte': rule.increment_byte,
                'is_extended': rule.is_extended,
                'delay_ms': rule.delay_ms,
                'comment': rule.comment,
                'enabled': rule.enabled
            })
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    
    def load_config(self, filename: str) -> Optional[dict]:
        """Load configuration from .cantroller file. Returns settings dict or None."""
        with open(filename, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Clear existing
        self.can_manager.clear_transmit_messages()
        self.can_manager.clear_response_rules()
        
        # Load periodic messages
        for msg_data in config.get('periodic_messages', []):
            msg = TransmitMessage(
                msg_id=msg_data['msg_id'],
                data=msg_data['data'],
                is_extended=msg_data.get('is_extended', True),
                cycle_time_ms=msg_data.get('cycle_time_ms', 100),
                increment_byte=msg_data.get('increment_byte', -1),
                is_paused=msg_data.get('is_paused', False),
                comment=msg_data.get('comment', '')
            )
            self.can_manager.add_transmit_message(msg)
        
        # Load response rules
        for rule_data in config.get('response_rules', []):
            rule = ResponseRule(
                trigger_id=rule_data['trigger_id'],
                response_id=rule_data['response_id'],
                response_data=rule_data['response_data'],
                increment_byte=rule_data.get('increment_byte', -1),
                is_extended=rule_data.get('is_extended', True),
                delay_ms=rule_data.get('delay_ms', 0),
                comment=rule_data.get('comment', ''),
                enabled=rule_data.get('enabled', True)
            )
            self.can_manager.add_response_rule(rule)
        
        return config.get('settings')
    
    # === Settings Persistence ===
    
    def save_settings(self, settings_file: str, current_file: Optional[str], display_mode: str):
        """Save application settings"""
        try:
            settings = {
                'last_file': current_file,
                'display_mode': display_mode,
                'id_database': {str(k): v for k, v in self.id_database.items()},
                'signal_database': {str(k): v for k, v in self.signal_database.items()},
                'name_to_id': self.name_to_id
            }
            with open(settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass
    
    def load_settings(self, settings_file: str) -> dict:
        """Load application settings. Returns dict with keys: display_mode, last_file"""
        result = {'display_mode': 'hex', 'last_file': None}
        try:
            if os.path.exists(settings_file):
                with open(settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                
                result['display_mode'] = settings.get('display_mode', 'hex')
                result['last_file'] = settings.get('last_file')
                
                # Restore databases
                id_db = settings.get('id_database', {})
                self.id_database = {int(k): v for k, v in id_db.items()}
                
                sig_db = settings.get('signal_database', {})
                self.signal_database = {int(k): v for k, v in sig_db.items()}
                
                self.name_to_id = settings.get('name_to_id', {})
        except Exception:
            pass
        return result
    
    # === Export Logs ===
    
    def export_logs(self, filename: str, format_type: str, 
                    receive_messages: Dict[int, dict]) -> None:
        """Export received messages to file (csv, txt, or asc)"""
        with open(filename, 'w', encoding='utf-8', newline='') as f:
            if format_type == 'csv':
                writer = csv.writer(f)
                writer.writerow(['Timestamp', 'CAN-ID', 'Name', 'Type', 'Length', 'Data', 'Count'])
                for msg_id, entry in sorted(receive_messages.items()):
                    msg = entry['msg']
                    id_hex = f"{msg_id:08X}" if msg.is_extended_id else f"{msg_id:03X}"
                    name = self.id_database.get(msg_id, '')
                    data_str = ' '.join(f"{b:02X}" for b in msg.data)
                    timestamp = datetime.fromtimestamp(entry['last_time']).strftime('%H:%M:%S.%f')[:-3]
                    writer.writerow([timestamp, id_hex, name, 'Ext' if msg.is_extended_id else 'Std', 
                                    len(msg.data), data_str, entry['count']])
            
            elif format_type == 'txt':
                for msg_id, entry in sorted(receive_messages.items()):
                    msg = entry['msg']
                    id_hex = f"{msg_id:08X}" if msg.is_extended_id else f"{msg_id:03X}"
                    data_str = ' '.join(f"{b:02X}" for b in msg.data)
                    timestamp = datetime.fromtimestamp(entry['last_time']).strftime('%H:%M:%S.%f')[:-3]
                    f.write(f"{timestamp}  {id_hex}  [{len(msg.data)}]  {data_str}\n")
            
            elif format_type == 'asc':
                f.write("date " + datetime.now().strftime("%a %b %d %I:%M:%S %p %Y") + "\n")
                f.write("base hex  timestamps absolute\n")
                f.write("Begin Triggerblock\n")
                for msg_id, entry in sorted(receive_messages.items()):
                    msg = entry['msg']
                    data_str = ' '.join(f"{b:02X}" for b in msg.data)
                    timestamp = entry['last_time']
                    f.write(f"   {timestamp:.6f} 1  {msg_id:08X}x       Rx   d {len(msg.data)}  {data_str}\n")
                f.write("End Triggerblock\n")
    
    # === Import CAN Databases ===
    
    def import_csv_blocks(self, filename: str) -> int:
        """Import CAN IDs from CSV file"""
        count = 0
        with open(filename, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            
            for row in reader:
                if len(row) < 3:
                    continue
                try:
                    name = row[1].strip()
                    can_id_str = row[2].strip().replace('0x', '').replace('h', '')
                    msg_id = int(can_id_str, 16)
                    
                    if msg_id and name:
                        self.id_database[msg_id] = name
                        self.name_to_id[name.upper()] = msg_id
                        count += 1
                except (ValueError, IndexError):
                    continue
        return count
    
    def import_md_blocks(self, filename: str) -> int:
        """Import CAN IDs from Notion-exported Markdown table"""
        count = 0
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            if not line.startswith('|') or '---' in line:
                continue
            
            parts = [p.strip() for p in line.split('|')[1:-1]]
            if len(parts) < 3 or 'CAN ID' in parts[2] or 'Name' in parts[1]:
                continue
            
            try:
                name = parts[1].strip().replace('**', '')
                can_id_str = parts[2].strip().replace('0x', '').replace('**', '')
                msg_id = int(can_id_str, 16)
                
                if msg_id and name:
                    self.id_database[msg_id] = name
                    self.name_to_id[name.upper()] = msg_id
                    count += 1
            except (ValueError, IndexError):
                continue
        return count
    
    def import_csv_signals(self, filename: str) -> int:
        """Import signals from CSV"""
        count = 0
        with open(filename, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            next(reader, None)
            
            for row in reader:
                if len(row) < 5:
                    continue
                try:
                    data_point = row[1].strip() if len(row) > 1 else ''
                    if data_point.lower().startswith('undef') or not data_point:
                        continue
                    
                    msg_id = int(row[0].strip().replace('0x', ''), 16)
                    signal_name = row[2].strip() if len(row) > 2 else data_point
                    
                    factor = 1.0
                    if len(row) > 5 and row[5].strip():
                        try:
                            factor = float(row[5].strip())
                        except ValueError:
                            factor = 1.0
                    
                    signal = {
                        'name': signal_name[:12],
                        'bit_start': int(row[3]),
                        'bit_length': int(row[4]),
                        'factor': factor,
                        'unit': row[6].strip() if len(row) > 6 and row[6] != '—' else ''
                    }
                    
                    if msg_id not in self.signal_database:
                        self.signal_database[msg_id] = []
                    self.signal_database[msg_id].append(signal)
                    count += 1
                except (ValueError, IndexError):
                    continue
        return count
    
    def import_md_signals(self, filename: str) -> int:
        """Import signals from Notion-exported Markdown table"""
        count = 0
        current_can_id = None
        
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            
            if line.startswith('###') and '(0x' in line:
                try:
                    hex_start = line.find('(0x') + 3
                    hex_end = line.find(')', hex_start)
                    if hex_end > hex_start:
                        current_can_id = int(line[hex_start:hex_end], 16)
                except ValueError:
                    current_can_id = None
                continue
            
            if not line.startswith('|') or '---' in line or current_can_id is None:
                continue
            
            parts = [p.strip().replace('**', '') for p in line.split('|')[1:-1]]
            if len(parts) < 4:
                continue
            
            if 'Signal' in parts[0] or 'Variable' in parts[0] or 'name' in parts[0].lower():
                continue
            
            try:
                name = parts[0].strip()
                if not name or name.startswith('Reserve') or name.startswith('---'):
                    continue
                
                try:
                    bit_start = int(parts[2].strip())
                except ValueError:
                    continue
                
                try:
                    bit_length = int(parts[3].strip())
                except ValueError:
                    bit_length = 8
                
                factor = 1.0
                if len(parts) > 4 and parts[4].strip():
                    try:
                        factor = float(parts[4].strip())
                    except ValueError:
                        factor = 1.0
                
                unit = parts[5].strip() if len(parts) > 5 else ''
                unit = unit.replace('?', '').strip()
                
                if current_can_id not in self.signal_database:
                    self.signal_database[current_can_id] = []
                self.signal_database[current_can_id].append({
                    'name': name[:15], 'bit_start': bit_start,
                    'bit_length': bit_length, 'factor': factor, 'unit': unit
                })
                count += 1
            except (ValueError, IndexError):
                continue
        
        return count
