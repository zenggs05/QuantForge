"""Observe research function entries without running any research calculation."""
import inspect
import sys


class Counter:
    def __init__(self):
        from sklearn.linear_model import Ridge
        from quantforge.diagnostics import forecast_tests
        self.functions = {'sklearn.Ridge.fit': inspect.unwrap(Ridge.fit)}
        for name in ('clark_west_differential', 'dm_differential', 'ic_product', 'r2_oos',
                     'run_size_power_monte_carlo', 'mde', 'normal_power',
                     'months_required', 'encompassing_approx'):
            self.functions[name] = getattr(forecast_tests, name)
        self.codes = {f.__code__: name for name, f in self.functions.items()}
        self.counts = dict.fromkeys(self.functions, 0)
        self.tool = sys.monitoring.PROFILER_ID

    def start(self):
        sys.monitoring.use_tool_id(self.tool, 'gate1_real_data_zero_counter')
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, self.enter)
        for code in self.codes:
            sys.monitoring.set_local_events(self.tool, code, sys.monitoring.events.PY_START)

    def enter(self, code, offset):
        self.counts[self.codes[code]] += 1
        raise RuntimeError('Gate 1 attempted research computation: ' + self.codes[code])

    def finish(self):
        for code in self.codes:
            sys.monitoring.set_local_events(self.tool, code, 0)
        sys.monitoring.free_tool_id(self.tool)
        return {
            'real_data_ridge_fits': self.counts['sklearn.Ridge.fit'],
            'real_data_statistical_tests_count': sum(self.counts[k] for k in (
                'clark_west_differential', 'dm_differential', 'ic_product', 'r2_oos')),
            'research_function_counts': self.counts,
            'counter_method': 'sys.monitoring PY_START on named function code objects, with immediate failure; CLI profile guard also active',
        }
