from trading_simulator import TradingSimulator, load_config

cfg = load_config("config/my_config.yaml")
sim = TradingSimulator(cfg)
result = sim.run()