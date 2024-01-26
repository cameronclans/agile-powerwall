import datetime as dt
import itertools


EXCLUSIVE_OFFSET = 0.000001

ONE_DAY_INCREMENT = dt.timedelta(days=1)
SLOT_TIME_INCREMENT = dt.timedelta(minutes=30)

CHARGE_NAMES = ["SUPER_OFF_PEAK", "OFF_PEAK", "PARTIAL_PEAK", "ON_PEAK"]

PRICE_CAP = 1.00


class Rates:
    def __init__(self):
        self.previous_day = None
        self.current_day = None
        self.next_day = None

    def is_valid(self):
        if self.previous_day is None or self.current_day is None or self.next_day is None:
            raise ValueError("Waiting for rate data")

        if len(self.previous_day) > 0 and len(self.current_day) > 0:
            previous_day_end = self.previous_day[-1]["end"]
            current_day_start = self.current_day[0]["start"]
            if current_day_start != previous_day_end:
                raise ValueError(f"Previous to current day rates are not contiguous: {previous_day_end} {current_day_start}")

        if len(self.current_day) > 0 and len(self.next_day) > 0:
            current_day_end = self.current_day[-1]["end"]
            next_day_start = self.next_day[0]["start"]
            if next_day_start != current_day_end:
                raise ValueError(f"Current to next day rates are not contiguous: {current_day_end} {next_day_start}")

    def between(self, start, end):
        if self.previous_day is not None and self.current_day is not None and self.next_day is not None:
            all_rates = itertools.chain(self.previous_day, self.current_day, self.next_day)
            return [rate for rate in all_rates if rate["start"] >= start and rate["end"] <= end]
        else:
            return []

    def clear(self):
        self.previous_day = None
        self.current_day = None
        self.next_day = None


class Schedule:
    def __init__(self, charge_name, import_pricing, export_pricing):
        self.charge_name = charge_name
        self.import_pricing = import_pricing
        self.export_pricing = export_pricing
        self._periods = []
        self._import_value = None
        self._export_value = None
        self._start = None
        self._end = None

    def add(self, import_rate, export_rate):
        if export_rate is not None:
            if import_rate["start"] != export_rate["start"]:
                raise ValueError(f"Import rate and export rate are not for the same period: import was {import_rate}, export was {export_rate}")
            if import_rate["end"] != export_rate["end"]:
                raise ValueError(f"Import rate and export rate are not for the same period: import was {import_rate}, export was {export_rate}")

        if self._start is None:
            self._start = import_rate["start"]
            self._end = import_rate["end"]
        elif import_rate['start'] == self._end:
            self._end = import_rate["end"]
        else:
            self._periods.append((self._start, self._end))
            self._start = import_rate["start"]
            self._end = import_rate["end"]
        self.import_pricing.add(import_rate["value_inc_vat"])
        if export_rate is not None:
            self.export_pricing.add(export_rate["value_inc_vat"])

    def get_periods(self):
        if self._start is not None:
            self._periods.append((self._start, self._end))
            self._start = None
            self._end = None
        return self._periods

    def get_import_value(self):
        if self._import_value is None:
            self._import_value = self.import_pricing.get_value()
        return self._import_value

    def get_export_value(self):
        if self._export_value is None:
            self._export_value = self.export_pricing.get_value()
        return self._export_value


def lowest_rates(rates, hrs):
    prices = [r["value_inc_vat"] for r in rates]
    prices.sort()
    n = round(2.0*float(hrs))
    limit = prices[n-1] if n <= len(prices) else prices[-1]
    return limit + EXCLUSIVE_OFFSET


def highest_rates(rates, hrs):
    prices = [r["value_inc_vat"] for r in rates]
    prices.sort(reverse=True)
    n = round(2.0*float(hrs))
    limit = prices[n-1] if n <= len(prices) else prices[-1]
    return limit + EXCLUSIVE_OFFSET


RATE_FUNCS = {
    "lowest": lowest_rates,
    "highest": highest_rates
}


class AveragePricing():
    def __init__(self):
        self.sum = 0
        self.count = 0

    def add(self, price):
        self.sum += price
        self.count += 1

    def get_value(self):
        if self.count > 0:
            v = self.sum/self.count
            if v < 0.0:
                v = 0.0
            return v
        else:
            return 0.0


class MinimumPricing():
    def __init__(self):
        self.min = PRICE_CAP

    def add(self, price):
        if price < self.min:
            self.min = price

    def get_value(self):
        v = self.min
        if v < 0.0:
            v = 0.0;
        return v


class MaximumPricing():
    def __init__(self):
        self.max = 0

    def add(self, price):
        if price > self.max:
            self.max = price

    def get_value(self):
        return self.max


class FixedPricing:
    def __init__(self, v):
        self.v = float(v)

    def add(self, price):
        pass

    def get_value(self):
        return self.v


PRICING_FUNCS = {
    "average": AveragePricing,
    "minimum": MinimumPricing,
    "maximum": MaximumPricing,
    "fixed": FixedPricing,
}


def create_pricing(pricing_expr):
    if '(' in pricing_expr and pricing_expr[-1] == ')':
        sep = pricing_expr.index('(')
        func_name = pricing_expr[:sep]
        func_args = pricing_expr[sep+1:-1].split(',')
    else:
        func_name = pricing_expr
        func_args = []
    pricing_type = PRICING_FUNCS[func_name]
    return pricing_type(*func_args)


def extend_from(rates, start_time):
    first = rates[0]
    while first["start"] > start_time:
        new_first = first.copy()
        new_first["start"] = first["start"] - SLOT_TIME_INCREMENT
        new_first["end"] = first["start"]
        rates.insert(0, new_first)
        first = new_first


def extend_to(rates, end_time):
    last = rates[-1]
    while last["end"] < end_time:
        new_last = last.copy()
        new_last["start"] = last["end"]
        new_last["end"] = last["end"] + SLOT_TIME_INCREMENT
        rates.append(new_last)
        last = new_last


def get_schedules(config, day_date, import_rates, export_rates):
    day_start = dt.datetime.combine(day_date, dt.time.min).astimezone(dt.timezone.utc)
    day_end = dt.datetime.combine(day_date + ONE_DAY_INCREMENT, dt.time.min).astimezone(dt.timezone.utc)

    # filter down to the given day
    day_import_rates = import_rates.between(day_start, day_end)
    day_export_rates = export_rates.between(day_start, day_end)

    if len(day_import_rates) == 0:
        return None

    # pad rates to cover 24 hours
    extend_from(day_import_rates, day_start)
    extend_to(day_import_rates, day_end)
    if len(day_export_rates) > 0:
        extend_from(day_export_rates, day_start)
        extend_to(day_export_rates, day_end)

    plunge_pricing = False
    for rate in day_import_rates:
        if rate["value_inc_vat"] < 0.0:
            plunge_pricing = True
            break

    if "plunge_pricing_tariff_breaks" in config and plunge_pricing:
        configured_breaks = config["plunge_pricing_tariff_breaks"]
    else:
        configured_breaks = config["tariff_breaks"]
    if len(configured_breaks) != len(CHARGE_NAMES)-1:
        raise ValueError(f"{len(CHARGE_NAMES)-1} breaks must be specified")

    breaks = []
    for br in configured_breaks:
        if isinstance(br, float) or isinstance(br, int):
            v = br
        elif isinstance(br, str) and '(' in br and br[-1] == ')':
            sep = br.index('(')
            func_name = br[:sep]
            func_args = br[sep+1:-1].split(',')
            v = RATE_FUNCS[func_name](day_import_rates, *func_args)
        else:
            raise ValueError(f"Invalid threshold: {br}")
        breaks.append(v)

    configured_import_pricing = config.get("import_tariff_pricing")
    if configured_import_pricing is None:
        configured_import_pricing = config["tariff_pricing"]
    if len(configured_import_pricing) != len(CHARGE_NAMES):
        raise ValueError(f"{len(CHARGE_NAMES)} import_pricing functions must be specified")

    configured_export_pricing = config.get("export_tariff_pricing", ["fixed(0.0)"] * len(CHARGE_NAMES))
    if len(configured_export_pricing) != len(CHARGE_NAMES):
        raise ValueError(f"{len(CHARGE_NAMES)} export_pricing functions must be specified")

    schedules = []
    for i, charge_name in enumerate(CHARGE_NAMES):
        import_pricing = create_pricing(configured_import_pricing[i])
        export_pricing = create_pricing(configured_export_pricing[i])
        schedules.append(Schedule(charge_name, import_pricing, export_pricing))

    for import_rate, export_rate in itertools.zip_longest(day_import_rates, day_export_rates):
        import_cost = import_rate['value_inc_vat']
        schedule = None
        for i, br in enumerate(breaks):
            if import_cost < br:
                schedule = schedules[i]
                break
        if schedule is None:
            schedule = schedules[-1]
        schedule.add(import_rate, export_rate)

    return schedules


def to_charge_period_json(start_day_of_week, end_day_of_week, period):
    start_local = period[0].astimezone()
    end_local = period[1].astimezone()
    return {
        "fromDayOfWeek": start_day_of_week,
        "fromHour": start_local.hour,
        "fromMinute": start_local.minute,
        "toDayOfWeek": end_day_of_week,
        "toHour": end_local.hour,
        "toMinute": end_local.minute
    }


def calculate_tariff_data(config, day_date, import_rates, export_rates):
    schedules = get_schedules(config, day_date, import_rates, export_rates)
    if schedules is None:
        return

    tou_periods = {}
    buy_price_info = {}
    sell_price_info = {}
    for schedule in schedules:
        charge_periods = []
        for period in schedule.get_periods():
            charge_periods.append(to_charge_period_json(0, 6, period))
        tou_periods[schedule.charge_name] = charge_periods
        buy_price_info[schedule.charge_name] = schedule.get_import_value()
        sell_price_info[schedule.charge_name] = schedule.get_export_value()

    plan = config["tariff_name"]
    provider = config["tariff_provider"]
    demand_changes = {"ALL": {"ALL": 0}, "Summer": {}, "Winter": {}}
    daily_charges = [{"name": "Charge", "amount": 0}]
    seasons = {"Summer": {"fromMonth": 1, "fromDay": 1, "toDay": 31,
                          "toMonth": 12, "tou_periods": tou_periods},
               "Winter": {"tou_periods": {}}}
    tariff_data = {
        "name": plan,
        "utility": provider,
        "daily_charges": daily_charges,
        "demand_charges": demand_changes,
        "seasons": seasons,
        "energy_charges": {"ALL": {"ALL": 0},
                        "Summer": buy_price_info,
                        "Winter": {}},
        "sell_tariff": {"name": plan,
                     "utility": provider,
                     "daily_charges": daily_charges,
                     "demand_charges": demand_changes,
                     "seasons": seasons,
                     "energy_charges": {"ALL": {"ALL": 0},
                                        "Summer": sell_price_info,
                                        "Winter": {}}}
    }
    return tariff_data
