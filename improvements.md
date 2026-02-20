# CANtroller — Plano de Melhorias e Análise Arquitetural

O **CANtroller** é uma ferramenta robusta para monitorizar, simular e interagir com barramentos CAN, focada no desenvolvimento de veículos elétricos (BMS, MCU). O uso de Python com PyQt6 e `python-can` é uma escolha tecnológica sólida.

As melhorias estão organizadas por **prioridade real** com base na análise detalhada do código-fonte.

---

## 🔴 Prioridade 1 — Desempenho e Estabilidade

### 1.1 Throttling / Batching do GUI
O `_receive_loop` em `can_manager.py` emite `message_received.emit(msg)` **por cada mensagem CAN** recebida, e o handler `_on_message_received` na `MainWindow` chama `_update_receive_table()` por cada sinal. Num barramento com 50+ IDs a 100ms, isto congela a interface.

**Solução:** O `CANManager` deve acumular mensagens num buffer thread-safe (`collections.deque` ou `queue.Queue`). A `MainWindow` consome o buffer via `QTimer` periódico (~30ms) e atualiza a tabela em batch.

```python
# can_manager.py — acumular em buffer
from collections import deque

class CANManager(QObject):
    def __init__(self):
        ...
        self._rx_buffer = deque(maxlen=10000)

    def _receive_loop(self):
        while self._running and self.bus:
            msg = self.bus.recv(timeout=0.1)
            if msg and not self._paused:
                self._rx_count += 1
                self._rx_buffer.append(msg)
                if self._response_mode_enabled:
                    self._check_and_respond(msg)

    def drain_rx_buffer(self):
        """Retorna e esvazia o buffer de mensagens recebidas."""
        msgs = list(self._rx_buffer)
        self._rx_buffer.clear()
        return msgs
```

```python
# main_window.py — consumir em batch
def __init__(self):
    ...
    self.rx_timer = QTimer()
    self.rx_timer.timeout.connect(self._process_rx_batch)
    self.rx_timer.start(33)  # ~30 FPS

def _process_rx_batch(self):
    msgs = self.can_manager.drain_rx_buffer()
    for msg in msgs:
        self._on_message_received(msg)
    if msgs:
        self._update_receive_table()
```

### 1.2 Bug: `time.sleep()` na Thread de Receção
Em `can_manager.py` linha 309, `_check_and_respond` usa `time.sleep(rule.delay_ms / 1000.0)` **dentro da thread de receção**. Isto bloqueia a receção de TODAS as mensagens durante o delay. Com um delay de 100ms num barramento cheio, perdem-se mensagens.

**Solução:** Usar `QTimer.singleShot` para agendar a resposta sem bloquear:

```python
def _check_and_respond(self, received_msg: can.Message):
    for rule in self._response_rules:
        if not rule.enabled:
            continue
        if received_msg.arbitration_id == rule.trigger_id:
            if rule.delay_ms > 0:
                # Agendar resposta sem bloquear a thread
                QTimer.singleShot(rule.delay_ms, lambda r=rule: self._send_response(r))
            else:
                self._send_response(rule)

def _send_response(self, rule: ResponseRule):
    if 0 <= rule.increment_byte < len(rule.response_data):
        rule.response_data[rule.increment_byte] = (
            rule.response_data[rule.increment_byte] + 1
        ) & 0xFF
    self.send_message(rule.response_id, rule.response_data, rule.is_extended)
```

### 1.3 `_emit_status` em cada `send_message`
O `send_message` chama `_emit_status()` a cada envio, emitindo um `pyqtSignal` com dicionário. Sob simulação a 250ms com 2 frames, são 8 emissões/segundo. Com auto-response ativo, multiplica.

**Solução:** Remover o `_emit_status()` de dentro do `send_message` e confiar no `status_timer` de 250ms que já existe na `MainWindow`.

---

## 🟠 Prioridade 2 — Arquitetura e Manutenção

### 2.1 Refatoração do God Object (`main_window.py`)
O ficheiro tem **2226 linhas** e **89 métodos**. A `MainWindow` concentra GUI, lógica de negócio, gestão de ficheiros e simulação.

**Solução incremental:**
1. Extrair `HexDataLineEdit`, `HexByteLineEdit` → `src/widgets/hex_inputs.py`
2. Extrair `AddRuleDialog`, `NewTransmitMessageDialog` → `src/dialogs/`
3. Extrair cada tab para um widget: `SimulationTab`, `ResponseRulesTab`, `PeriodicMessagesTab` → `src/tabs/`
4. Extrair lógica de save/load/export → `src/config_manager.py`
5. A `MainWindow` ficaria como orquestrador (~400-500 linhas)

### 2.2 Suporte a Ficheiros DBC (Standard da Indústria)
Atualmente o CANtroller importa sinais via CSV custom. No mundo CAN, o formato universal é o **`.dbc`** (Database CAN). A biblioteca `cantools` faz parsing nativo de DBC.

**Impacto:** Permitiria importar diretamente bases de dados de qualquer fornecedor de ECUs sem conversão manual.

```python
# Exemplo com cantools
import cantools
db = cantools.database.load_file('vehicle.dbc')
msg = db.get_message_by_frame_id(0x18F81280)
decoded = msg.decode(data_bytes)
# → {'Voltage': 72.0, 'Current': 30.0, 'SOC': 85}
```

### 2.3 Suporte CAN FD (Preparação Futura)
O código assume frames CAN clássicos de 8 bytes máximo (hardcoded em vários sítios). CAN FD suporta até 64 bytes por frame e já é usado em muitos veículos novos. Abstrair o tamanho máximo de frame agora evita refatoração futura.

---

## 🟡 Prioridade 3 — Qualidade e Funcionalidade

### 3.1 Testes Unitários
Não existem testes automatizados. As funções `encode_bms_frame` e `encode_mcu_frame` são candidatas perfeitas — se um bit mudar, o display real mostra dados errados.

**Áreas críticas a testar:**
- `encode_bms_frame()` / `encode_mcu_frame()` — encoders de bits
- `TripProfileGenerator._voltage_from_soc()` — curva NMC
- Parsing de CSV (`load_csv_profile`)
- `ResponseRule` matching e increment logic

### 3.2 Simulação Configurável
O `TripProfileGenerator` tem constantes hardcoded (NMC 20S 72V). Para suportar outros veículos, extrair para ficheiros de configuração `.json` ou `.yaml`:

```json
{
  "name": "AJP PR7 630",
  "chemistry": "NMC",
  "cells_series": 20,
  "voltage_full": 84.0,
  "voltage_nominal": 72.0,
  "voltage_empty": 60.0,
  "capacity_ah": 73.0,
  "max_continuous_a": 110.0
}
```

### 3.3 Replay de Ficheiros de Log
A exportação `.asc` já existe no menu. O que falta é o **import/replay** — carregar um ficheiro `.asc` ou `.trc` e reproduzir o tráfego no CANtroller como se fosse em tempo real. Isto é fundamental para depuração offline.

---

## 🟢 Prioridade 4 — Nice-to-Have / Futuro

### 4.1 ~~Live Graphing~~ → Usar SavvyCAN
O **SavvyCAN** já oferece graphing em tempo real, replay de logs `.asc`, suporte DBC nativo, e é gratuito. Não vale a pena duplicar esta funcionalidade no CANtroller. O foco do CANtroller deve ser: **auto-response, simulação, byte increment**. Para análise visual e graphing, usar SavvyCAN em paralelo.

### 4.2 Melhorias ao Modelo Matemático da Bateria
O modelo atual usa uma curva `V(SOC)` polinomial simples com ruído gaussiano. Para simulações mais realistas, considerar:

- **Resistência interna**: Modelar queda de tensão sob carga (`V_terminal = V_OCV - I × R_internal`). A resistência interna varia com SOC e temperatura.
- **Efeito da temperatura**: O SOC disponível e a tensão nominal variam com a temperatura. A -10°C, a capacidade pode cair 30-40%.
- **Curva de Peukert**: A capacidade efetiva diminui com correntes de descarga elevadas.
- **Modelagem térmica**: Aquecimento interno baseado em `I² × R_internal × dt`, com dissipação por convecção.

Estes melhoramentos tornam-se importantes quando as mensagens de temperatura das células forem adicionadas.

### 4.3 Mensagens de Temperatura das Células (BMS)
Adicionar um novo CAN frame BMS que transmita as temperaturas individuais das células (ou por grupos de células). Parâmetros a incluir:

- Temperatura mínima / máxima / média do pack
- Temperaturas por módulo (ou por sensor NTC)
- Integração com o simulador para gerar perfis de temperatura realistas baseados no perfil de carga/descarga

Isto é essencial porque a temperatura afeta diretamente: capacidade disponível, resistência interna, limites de corrente de carga, e tempo de vida da bateria.

### 4.4 Fault Injection (Teste de Erros)
Capacidade de injetar flags de erro nos frames CAN para testar o comportamento do display e da VCU perante condições de falha. Exemplos:

- **Overvoltage / Undervoltage** — simular células fora de limites
- **Overcurrent** — corrente acima do máximo permitido
- **Overtemperature** — temperatura acima do limite de operação
- **Communication Timeout** — parar de enviar frames para testar watchdog do display
- **SOC Inconsistency** — SOC que não corresponde à tensão
- **Cell Imbalance** — diferença de tensão entre células acima do limiar

Implementação: botões ou checkboxes no tab de simulação que ativam/desativam cada tipo de falha, alterando os bytes correspondentes nos frames BMS transmitidos.

### 4.5 Tema Global
O tema CSS em `main.py` (~180 linhas) funciona bem. Bibliotecas como `qdarktheme` podem simplificar mas não são prioritárias.

---

## ✅ Implementado (v1.2)

| Melhoria | Estado |
|---|---|
| Throttling / Batching do GUI | ✅ Implementado |
| Fix `time.sleep()` na thread de receção | ✅ Implementado |
| Remover `_emit_status()` de `send_message` | ✅ Implementado |
| Refatoração do `main_window.py` (2226 → ~1370 linhas) | ✅ Implementado |

---

## Resumo Estratégico

| Fase | Foco | Impacto |
|---|---|---|
| ~~Imediato~~ | ~~Throttling do GUI + fix do `time.sleep`~~ | ✅ Feito |
| ~~Curto prazo~~ | ~~Refatoração do `main_window.py`~~ | ✅ Feito |
| **Médio prazo** | Suporte DBC + testes unitários + temperaturas células | Profissionalismo e qualidade |
| **Longo prazo** | Fault injection + CAN FD + modelo bateria avançado | Funcionalidades avançadas |
| **Análise visual** | Usar SavvyCAN (não reinventar a roda) | — |
