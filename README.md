# CANtroller

<p align="center">
  <img src="docs/screenshot.png" alt="CANtroller Screenshot" width="800">
</p>

**CANtroller** is an intelligent CAN bus monitoring and control tool with automatic response capabilities. Built with Python and PyQt6, it provides a professional interface similar to PCAN-View with added intelligent features.

## ✨ Features

- 🔍 **Real-time CAN Monitoring** - View all CAN messages with ID, data, cycle time, and count
- 📧 **Periodic Message Transmission** - Send messages at configurable intervals
- 🔄 **Intelligent Auto-Response** - Automatically respond to specific CAN IDs with custom data
- 📊 **Signal Decoding** - Decode CAN data into readable values (Speed:20km/h, Voltage:100V)
- 📥 **CSV Import** - Import CAN IDs and signal definitions from CSV files
- 🔢 **3-Mode Data Display** - Toggle between HEX, Decimal, and Decoded views
- 🎨 **Modern Dark Theme** - Professional and eye-friendly interface
- 💾 **Save/Load Configuration** - Persist your messages and rules in `.cantroller` files
- 🔎 **Message Filtering** - Quick filter by CAN ID
- 📊 **Detailed Status Bar** - RX/TX counts, error tracking, connection status

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- PCAN-USB adapter with drivers installed

### Installation

```bash
# Clone the repository
git clone https://github.com/joaopef/CANtroller.git
cd CANtroller

# Install dependencies
pip install -r requirements.txt

# Run the application
python src/main.py
```

### Download Executable

For Windows users, download the pre-built executable from the [Releases](https://github.com/joaopef/CANtroller/releases) page.

## 📖 Usage

### Connecting to CAN Bus

1. Select your PCAN channel (PCAN_USBBUS1, etc.)
2. Choose the bitrate (125k, 250k, 500k, 1M)
3. Click **Connect**

### Periodic Messages

1. Go to **Transmit → New Message** or press `Ins`
2. Configure the CAN ID, data bytes, and cycle time
3. Messages will transmit automatically when connected

### Auto-Response Rules

1. Switch to the **Response Rules** tab
2. Click **Add Rule**
3. Set the trigger ID (incoming message to react to)
4. Set the response ID and data to send back
5. Enable **Response Mode** to activate

### Saving Configuration

- **Ctrl+S** - Save current configuration
- **Ctrl+O** - Open a saved configuration
- **Ctrl+N** - New configuration (clear all)

### Signal Decoding (v1.2)

1. Go to **File → Import → Import CAN Blocks** and select your CSV file
2. Go to **File → Import → Import Signal Definitions** and select your data points CSV
3. Click on the **Data** column header to cycle through: HEX → Decimal → Decoded
4. In Decoded mode, signals display as `Speed:20km/h Voltage:100V`

**CSV Formats:**

*CAN Blocks.csv:*
```csv
CAN bus Nr,Name,CAN ID [hex],Ext,Send period max [ms],...
CAN_BUS_0,GET_SOC_1,0x18F81280,1,0,...
```

*CAN Data Points.csv:*
```csv
CAN ID,CAN Data Point,Signal name,Bit start,Bit length,Factor,Unit
0x18F86890,SPEED,Current speed,0,8,1,km/h
```

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CANtroller                           │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │  main.py    │───▶│ main_window │◀───│ can_manager │ │
│  │  (Entry)    │    │   (GUI)     │    │   (Logic)   │ │
│  └─────────────┘    └─────────────┘    └─────────────┘ │
│                            │                  │         │
│                            ▼                  ▼         │
│                     ┌─────────────┐    ┌───────────┐   │
│                     │   PyQt6     │    │python-can │   │
│                     └─────────────┘    └───────────┘   │
│                                              │         │
│                                              ▼         │
│                                       ┌───────────┐   │
│                                       │ PCAN-USB  │   │
│                                       └───────────┘   │
└─────────────────────────────────────────────────────────┘
```

## 📁 Project Structure

```
CANtroller/
├── docs/                    # Documentation
│   └── architecture.md      # Architecture details
├── src/                     # Source code
│   ├── main.py              # Application entry point + dark theme
│   ├── main_window.py       # GUI implementation (~1300 lines)
│   └── can_manager.py       # CAN communication logic (~310 lines)
├── .gitignore
├── LICENSE                  # MIT License
├── README.md
├── requirements.txt         # Python dependencies
└── CANtroller.spec          # PyInstaller configuration
```

## 🛠️ Building Executable

```bash
# Install PyInstaller
pip install pyinstaller

# Build the executable
pyinstaller --onefile --windowed --name "CANtroller" \
  --hidden-import=can.interfaces.pcan \
  --hidden-import=can.interfaces \
  --hidden-import=can.interfaces.virtual \
  --collect-submodules can \
  src/main.py

# The executable will be in dist/CANtroller.exe
```

## 📋 Configuration File Format

The `.cantroller` file is a JSON file containing:

```json
{
  "version": "1.0",
  "settings": {
    "channel": "PCAN_USBBUS1",
    "bitrate": "500 kbit/s"
  },
  "periodic_messages": [...],
  "response_rules": [...]
}
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 👤 Author

**João Ferreira**

- GitHub: [@joaopef](https://github.com/joaopef)

---

<p align="center">
  Made with ❤️ for the automotive community
</p>
