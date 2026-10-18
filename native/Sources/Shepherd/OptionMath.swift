import Foundation

// Black-Scholes repricing for the 期权计划 page, so the sliders answer instantly without asking Python.
// Must stay numerically identical to workbench/optionviz.py (test_option_math_parity.py compiles this file alone and compares).
// No dependency on the app's other types, so that test can build it by itself.
// European model: no skew, no early exercise, no dividends. A different volatility per leg is allowed.
enum OptMath {
    static let rate = 0.045

    static func ncdf(_ x: Double) -> Double { 0.5 * (1.0 + erf(x / 2.0.squareRoot())) }

    /// Per-share option value. At or past expiry (T <= 0) it is intrinsic.
    static func price(_ right: String, S: Double, K: Double, T: Double, iv: Double) -> Double {
        if T <= 0 || iv <= 0 || S <= 0 { return right == "C" ? max(S - K, 0) : max(K - S, 0) }
        let d1 = (log(S / K) + (rate + iv * iv / 2.0) * T) / (iv * T.squareRoot())
        let d2 = d1 - iv * T.squareRoot()
        if right == "C" { return S * ncdf(d1) - K * exp(-rate * T) * ncdf(d2) }
        return K * exp(-rate * T) * ncdf(-d2) - S * ncdf(-d1)
    }
}

struct OptLeg {
    let right: String      // "C" or "P"
    let strike: Double
    let dte: Double        // calendar days from today to this leg's expiry
    let qty: Double        // signed contracts (buy > 0, sell < 0), multiples included
    let mult: Double
    let iv: Double
}

/// One structure priced from the "calc" block that workbench/optionviz.py puts on every card (decoding lives in OptionPlansView.swift).
struct OptModel {
    let spot: Double
    let cost: Double       // signed dollars paid today (debit > 0, credit < 0), for the card's own contract count
    let contracts: Double
    let firstDTE: Double
    let legs: [OptLeg]

    init(spot: Double, cost: Double, contracts: Double, firstDTE: Double, legs: [OptLeg]) {
        self.spot = spot; self.cost = cost; self.contracts = max(contracts, 1); self.firstDTE = firstDTE; self.legs = legs
    }

    /// Dollar value of the whole structure with the underlying at S, `days` calendar days from today,
    /// every leg's volatility multiplied by `ivScale` (floored at 5%).
    func value(_ S: Double, days: Double, ivScale: Double) -> Double {
        legs.reduce(0) { sum, l in
            sum + l.qty * l.mult * OptMath.price(l.right, S: S, K: l.strike, T: max(l.dte - days, 0) / 365.0, iv: max(l.iv * ivScale, 0.05))
        }
    }
    func pnl(_ S: Double, days: Double, ivScale: Double) -> Double { value(S, days: days, ivScale: ivScale) - cost }
    var averageIV: Double { legs.isEmpty ? 0 : legs.map(\.iv).reduce(0, +) / Double(legs.count) }
}
