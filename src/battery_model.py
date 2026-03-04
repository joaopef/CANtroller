"""
Battery Model — PyBaMM-based battery simulation for CANtroller.

Uses PyBaMM's Single Particle Model (SPM) with lumped thermal model
to simulate realistic voltage, SOC, and temperature dynamics
for a 20S NMC pouch cell pack (72V nominal, 73Ah).

Falls back gracefully if PyBaMM is not installed.
"""

import numpy as np

# Try importing PyBaMM — set availability flag
try:
    import pybamm
    PYBAMM_AVAILABLE = True
except ImportError:
    PYBAMM_AVAILABLE = False


class PyBaMMBatteryModel:
    """
    Wraps PyBaMM SPM with lumped thermal model for a 20S NMC pack.

    Usage:
        model = PyBaMMBatteryModel(num_cells_series=20, capacity_ah=73.0)
        result = model.simulate_drive_cycle(times, currents, ambient_temp_C=25.0)
        # result = {'voltage': [...], 'soc': [...], 'temperature': [...]}
    """

    def __init__(self, num_cells_series: int = 20,
                 capacity_ah: float = 40.0,
                 initial_soc: float = 0.85):
        """
        Args:
            num_cells_series: Number of cells in series (pack config).
            capacity_ah: Pack capacity in Ah.
            initial_soc: Initial state of charge (0.0 - 1.0).
        """
        if not PYBAMM_AVAILABLE:
            raise RuntimeError("PyBaMM is not installed. Install with: pip install pybamm")

        self.num_cells_series = num_cells_series
        self.capacity_ah = capacity_ah
        self.initial_soc = initial_soc

        # Build the SPM with lumped thermal model
        self._model = pybamm.lithium_ion.SPM(
            options={"thermal": "lumped"},
            name="CANtroller NMC Battery"
        )

        # Use default NMC parameter set (Chen2020)
        self._param = self._model.default_parameter_values

        # Store solver for reuse
        self._solver = pybamm.CasadiSolver(mode="safe")

    def simulate_drive_cycle(self, times: list, currents: list,
                             ambient_temp_C: float = 25.0) -> dict:
        """
        Simulate a drive cycle and return voltage, SOC, and temperature.

        Args:
            times: Array of time points in seconds (monotonically increasing).
            currents: Array of pack currents in Amperes (positive = discharge).
                      Must be the same length as times.
            ambient_temp_C: Ambient temperature in °C.

        Returns:
            dict with keys:
                'voltage': list of pack voltages (V)
                'soc': list of SOC values (0-100%)
                'temperature': list of cell temperatures (°C)
        """
        times = np.array(times, dtype=float)
        currents = np.array(currents, dtype=float)

        if len(times) != len(currents):
            raise ValueError("times and currents must have the same length")

        if len(times) < 2:
            raise ValueError("Need at least 2 data points")

        # Scale pack current to cell-level current
        # The default Chen2020 cell is ~5Ah; our pack is 73Ah (1P config)
        # We scale the current to match the model's nominal cell capacity
        cell_capacity = self._param["Nominal cell capacity [A.h]"]
        current_scale = cell_capacity / self.capacity_ah
        cell_currents = currents * current_scale

        # PyBaMM expects current as a function of time
        current_interp = pybamm.Interpolant(
            times, cell_currents, pybamm.t
        )

        # Set parameters for this simulation
        param = self._param.copy()
        param["Current function [A]"] = current_interp
        param["Initial temperature [K]"] = 273.15 + ambient_temp_C
        param["Ambient temperature [K]"] = 273.15 + ambient_temp_C

        # Create simulation with proper initial SOC
        sim = pybamm.Simulation(
            self._model,
            parameter_values=param,
            solver=self._solver
        )

        try:
            solution = sim.solve(
                t_eval=times,
                initial_soc=self.initial_soc
            )
        except pybamm.SolverError as e:
            # If solver fails (e.g., extreme currents), return None to signal fallback
            print(f"[PyBaMM] Solver failed: {e}. Keeping fallback values.")
            return None

        # Extract results
        cell_voltage = solution["Voltage [V]"].entries
        discharge_ah = solution["Discharge capacity [A.h]"].entries
        temperature_K = solution["X-averaged cell temperature [K]"].entries

        # Scale cell voltage to pack voltage (20S)
        pack_voltage = cell_voltage * self.num_cells_series

        # Convert discharge capacity to SOC percentage
        # Scale discharge capacity back to pack level
        pack_discharge_ah = discharge_ah / current_scale
        soc_pct = (self.initial_soc - pack_discharge_ah / self.capacity_ah) * 100.0
        soc_pct = np.clip(soc_pct, 0.0, 100.0)

        # Convert temperature from Kelvin to Celsius
        temperature_C = temperature_K - 273.15

        return {
            'voltage': pack_voltage.tolist(),
            'soc': soc_pct.tolist(),
            'temperature': temperature_C.tolist(),
        }
