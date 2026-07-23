# trading-tools

Standalone MCP server with market data tools for quantitative trading — OHLCV, fundamentals, news, sentiment, and macro data.

## Tools

| Tool | Description |
|------|-------------|
| `stock_profile` | Company profile (name, sector, market cap, description) |
| `instrument_context` | Instrument identity, lot size, exchange metadata |
| `financial_statements` | Income statement, balance sheet, cash flow |
| `sector_info` | Sector/industry classification and metrics |
| `market_data` | OHLCV historical data with technical indicators |
| `technical_indicators` | EMA, RSI, MACD, Bollinger Bands, ATR, and more |
| `market_snapshot` | Real-time market overview (indices, breadth) |
| `company_news` | Company-specific news feed |
| `global_news` | Macro and market-wide news |
| `social_sentiment` | StockTwits / Reddit / Futu social sentiment |
| `fred_data` | FRED economic data series |
| `prediction_market` | Polymarket prediction market data |

## Quick Start

### Install

```bash
# From source (development)
pip install -e ".[futu]"

# Or via uvx
uvx --from . trading-tools-mcp
```

### MCP Client Config (Cursor / Claude Desktop / Windsurf)

**Basic (no API keys):**

```json
{
  "mcpServers": {
    "trading-tools": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/haibingzhao/trading-tools", "trading-tools-mcp"]
    }
  }
}
```

**With API keys (recommended):**

```json
{
  "mcpServers": {
    "trading-tools": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/haibingzhao/trading-tools", "trading-tools-mcp"],
      "env": {
        "FINNHUB_API_KEY": "your-finnhub-key",
        "FRED_API_KEY": "your-fred-key",
        "ALPHA_VANTAGE_API_KEY": "your-alphavantage-key"
      }
    }
  }
}
```

> API keys are optional. Tools will fall back to sources that don't require keys when a key is not set.

## Data Sources

| Source | Coverage | API Key Required |
|--------|----------|------------------|
| Yahoo Finance | US, HK, Crypto | No |
| Finnhub | Global news, company news | [Yes (free tier)](https://finnhub.io/register) |
| FRED | US macro data | [Yes (free tier)](https://fred.stlouisfed.org/docs/api/api_key.html) |
| AlphaVantage | Global news, fundamentals | [Yes (free tier)](https://www.alphavantage.co/support/#api-key) |
| Futu OpenD | HK, A-share (real-time) | No (local daemon) |
| Polymarket | Prediction markets | No |
| StockTwits | US social sentiment | No |

### Environment Variables

Create a `.env` file or set environment variables:

```bash
# Optional — enables additional data sources
FINNHUB_API_KEY=
FRED_API_KEY=
ALPHA_VANTAGE_API_KEY=

# Futu OpenD (HK / A-share data)
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
```

## Requirements

- Python >= 3.10
- Dependencies: fastmcp, pandas, stockstats, requests

## License

MIT
