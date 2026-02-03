# CANtroller Architecture

## System Overview

```mermaid
flowchart TB
    subgraph Application["CANtroller Application"]
        subgraph GUI["GUI Layer (PyQt6)"]
            MW[MainWindow]
            RD[AddRuleDialog]
            TD[NewTransmitMessageDialog]
            HEX[HexDataLineEdit]
        end
        
        subgraph Logic["Business Logic"]
            CM[CANManager]
            RR[ResponseRule]
            TM[TransmitMessage]
        end
        
        subgraph Persistence["Data Persistence"]
            JSON[".cantroller JSON"]
        end
    end
    
    subgraph External["External Dependencies"]
        PCAN[PCAN-USB Adapter]
        CAN[CAN Bus Network]
        VCU[VCU / ECU]
    end
    
    MW --> CM
    MW --> JSON
    CM --> PCAN
    PCAN --> CAN
    CAN --> VCU
```

## Component Details

### main.py
- Application entry point
- Dark theme stylesheet definition
- QApplication initialization

### main_window.py (~1300 lines)
- `MainWindow` - Main application window
- `AddRuleDialog` - Dialog for response rules
- `NewTransmitMessageDialog` - Dialog for periodic messages  
- `HexDataLineEdit` - Auto-formatting hex input
- `HexByteLineEdit` - Single byte input with auto-tab

### can_manager.py (~310 lines)
- `CANManager` - CAN bus communication handler
- `ResponseRule` - Auto-response rule dataclass
- `TransmitMessage` - Periodic message dataclass

## Data Flow

```mermaid
sequenceDiagram
    participant User
    participant GUI as MainWindow
    participant MGR as CANManager
    participant BUS as PCAN-USB
    participant NET as CAN Network
    
    User->>GUI: Click Connect
    GUI->>MGR: connect(channel, bitrate)
    MGR->>BUS: Initialize
    BUS-->>MGR: Connected
    MGR-->>GUI: connection_changed signal
    
    loop Receive Loop
        NET->>BUS: CAN Message
        BUS->>MGR: bus.recv()
        MGR->>MGR: Check Response Rules
        alt Rule Match
            MGR->>BUS: Send Response
            BUS->>NET: Response Message
        end
        MGR->>GUI: message_received signal
        GUI->>GUI: Update Table
    end
```

## Configuration File Format

```json
{
  "version": "1.0",
  "settings": {
    "channel": "PCAN_USBBUS1",
    "bitrate": "500 kbit/s"
  },
  "periodic_messages": [
    {
      "msg_id": 418381314,
      "data": [3, 232, 0, 100, 0, 50, 0, 0],
      "is_extended": true,
      "cycle_time_ms": 100,
      "is_paused": false,
      "comment": "Example Message"
    }
  ],
  "response_rules": [
    {
      "trigger_id": 418381376,
      "response_id": 418397186,
      "response_data": [3, 232, 0, 100, 0, 50, 0, 0],
      "is_extended": true,
      "delay_ms": 10,
      "comment": "Auto Response",
      "enabled": true
    }
  ]
}
```

## Class Diagram

```mermaid
classDiagram
    class CANManager {
        -bus: can.Bus
        -_response_rules: List[ResponseRule]
        -_transmit_messages: List[TransmitMessage]
        -_rx_count: int
        -_tx_count: int
        +connect(channel, bitrate) bool
        +disconnect()
        +send_message(id, data, extended) bool
        +add_response_rule(rule)
        +add_transmit_message(msg)
        +message_received: pyqtSignal
        +message_sent: pyqtSignal
    }
    
    class ResponseRule {
        +trigger_id: int
        +response_id: int
        +response_data: List[int]
        +is_extended: bool
        +delay_ms: int
        +comment: str
        +enabled: bool
    }
    
    class TransmitMessage {
        +msg_id: int
        +data: List[int]
        +is_extended: bool
        +cycle_time_ms: int
        +is_paused: bool
        +comment: str
        +count: int
    }
    
    class MainWindow {
        -can_manager: CANManager
        -receive_messages: Dict
        -current_file: str
        +_connect()
        +_disconnect()
        +_save_config()
        +_open_config()
    }
    
    MainWindow --> CANManager
    CANManager --> ResponseRule
    CANManager --> TransmitMessage
```
