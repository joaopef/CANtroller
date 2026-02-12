# CANtroller Architecture

## System Overview

```mermaid
flowchart TB
    subgraph Application["CANtroller Application"]
        subgraph GUI["GUI Layer (PyQt6)"]
            MW[MainWindow]
            RD[AddRuleDialog]
            TD[NewTransmitMessageDialog]
            HEX[HexDataLineEdit / HexByteLineEdit]
        end
        
        subgraph Logic["Business Logic"]
            CM[CANManager]
            RR[ResponseRule]
            TM[TransmitMessage]
        end
        
        subgraph Simulation["EV Simulation"]
            SE[SimulationEngine]
            TPG[TripProfileGenerator]
            TP[TripProfile / TripDataPoint]
        end
        
        subgraph Persistence["Data Persistence"]
            JSON[".cantroller JSON"]
            CSV["CAN CSV Databases"]
        end
    end
    
    subgraph External["External Dependencies"]
        PCAN[PCAN-USB Adapter]
        CAN[CAN Bus Network]
        VCU[VCU / ECU / Display]
    end
    
    MW --> CM
    MW --> SE
    MW --> JSON
    SE --> CM
    SE --> TPG
    TPG --> TP
    CM --> PCAN
    PCAN --> CAN
    CAN --> VCU
```

## Component Details

### main.py
- Application entry point
- Dark theme stylesheet definition
- QApplication initialization

### main_window.py (~2200 lines)
- `MainWindow` — Main application window with receive/transmit/simulation tabs
- `AddRuleDialog` — Dialog for creating/editing response rules (with increment byte option)
- `NewTransmitMessageDialog` — Dialog for periodic messages (with increment byte option)
- `HexDataLineEdit` — Auto-formatting hex input with space separation
- `HexByteLineEdit` — Single byte input with auto-tab to next field

### can_manager.py (~320 lines)
- `CANManager` — CAN bus communication handler (connect, send, receive, auto-respond)
- `ResponseRule` — Auto-response rule dataclass (with `increment_byte` field)
- `TransmitMessage` — Periodic message dataclass (with `increment_byte` field)

### simulator.py (~600 lines)
- `SimulationEngine` — Plays trip profiles by encoding and sending BMS/MCU CAN frames
- `TripProfileGenerator` — Generates synthetic profiles (city, highway, charge) or loads CSV data
- `TripProfile` / `TripDataPoint` — Data structures for trip data
- `encode_bms_frame()` / `encode_mcu_frame()` — Big-endian CAN frame encoding

## Data Flow

```mermaid
sequenceDiagram
    participant User
    participant GUI as MainWindow
    participant MGR as CANManager
    participant SIM as SimulationEngine
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
            MGR->>MGR: Auto-increment byte (optional)
            MGR->>BUS: Send Response
            BUS->>NET: Response Message
        end
        MGR->>GUI: message_received signal
        GUI->>GUI: Update Table (HEX / Decimal / Decoded)
    end

    User->>GUI: Start Simulation
    GUI->>SIM: start(profile)
    loop Simulation Tick
        SIM->>SIM: Get TripDataPoint
        SIM->>SIM: encode_bms_frame + encode_mcu_frame
        SIM->>MGR: send_message(BMS_CAN_ID, data)
        SIM->>MGR: send_message(MCU_CAN_ID, data)
        SIM-->>GUI: data_updated signal
    end
```

## Simulation Engine

### Battery Model
| Parameter | Value |
|---|---|
| Chemistry | NMC Pouch Cells |
| Configuration | 20S |
| Nominal Voltage | 72V |
| Full Charge | 84V (4.2V/cell) |
| Cutoff | 60V (3.0V/cell) |
| Capacity | 73Ah (5256Wh) |
| Max Continuous | 110A |
| Peak (5s) | 250A |

### CAN Frame Encoding (Big-Endian)

**BMS Frame** (`0x18F81280` — GET_SOC_1):
| Bytes | Content | Encoding |
|---|---|---|
| 0-1 | Voltage | raw × 10 (e.g., 720 = 72.0V) |
| 2-3 | Current | raw × 20, signed (e.g., 600 = 30.0A) |
| 4 | SOC | 0-100% |
| 5-7 | Reserved | 0x00 |

**MCU Frame** (`0x18F86890` — GET_MCU_1):
| Bytes | Content | Encoding |
|---|---|---|
| 0 | Speed | km/h (0-255) |
| 1-3 | Total mileage | km (24-bit) |
| 4 | Current mileage | km (0-255) |
| 5 | Gear | 0=Park, 1=ECO, 2=Normal, 3=Sport |
| 6-7 | Reserved | 0x00 |

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
      "comment": "BMS Response",
      "increment_byte": -1
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
      "enabled": true,
      "increment_byte": 0
    }
  ]
}
```

> **Note:** `increment_byte` — Set to `-1` to disable, or `0-7` to auto-increment that byte position on each send (wraps 255 → 0).

## Class Diagram

```mermaid
classDiagram
    class CANManager {
        -bus: can.Bus
        -_response_rules: List~ResponseRule~
        -_transmit_messages: List~TransmitMessage~
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
        +response_data: List~int~
        +is_extended: bool
        +delay_ms: int
        +comment: str
        +enabled: bool
        +increment_byte: int
    }

    class TransmitMessage {
        +msg_id: int
        +data: List~int~
        +is_extended: bool
        +cycle_time_ms: int
        +is_paused: bool
        +comment: str
        +count: int
        +increment_byte: int
    }

    class SimulationEngine {
        -_can_manager: CANManager
        -_profile: TripProfile
        -_current_index: int
        -_playback_speed: float
        +start(profile)
        +pause()
        +stop()
        +data_updated: pyqtSignal
    }

    class TripProfileGenerator {
        +generate_city_trip()$ TripProfile
        +generate_highway_trip()$ TripProfile
        +generate_charge_session()$ TripProfile
        +load_csv_profile(filepath)$ TripProfile
    }

    class MainWindow {
        -can_manager: CANManager
        -sim_engine: SimulationEngine
        -signal_database: Dict
        +_connect()
        +_disconnect()
        +_save_config()
        +_open_config()
    }

    MainWindow --> CANManager
    MainWindow --> SimulationEngine
    SimulationEngine --> CANManager
    SimulationEngine --> TripProfileGenerator
    CANManager --> ResponseRule
    CANManager --> TransmitMessage
```
