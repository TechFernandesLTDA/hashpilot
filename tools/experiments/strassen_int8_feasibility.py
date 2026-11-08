"""Viabilidade do Strassen de 1 nível em INT8 exato — só CPU, roda em qualquer sistema.

NÃO minera, não gera prova, não fala com pool. Responde três perguntas que
decidem se vale escrever um kernel (o portão de decisão da ideia 2):

1. O Strassen ideal em inteiros é exato? (sim, é álgebra sobre Z)
2. Os operandos somados cabem em INT8? As entradas do Pearl vão de -64 a +64;
   somas/diferenças ficam em [-128, +128]. INT8 é [-128, +127], então o ÚNICO
   valor fora da faixa é +128 (ocorre só quando os dois valores são +64).
3. Uma correção barata (termo de posto 1 sobre as poucas posições == +128)
   restaura a exatidão sem estourar o INT8?

O que este script NÃO decide, e continua sendo o bloqueio real: se o transcript
do Pearl exige o produto intermediário de cada fatia de K separadamente. Se
exigir, não dá para substituir o GEMM do bloco pela recombinação do Strassen.
Isso depende de ler o código oficial (noisy_gemm / inner_hash), não deste teste.
"""
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

LOW, HIGH = -64, 64  # Faixa das entradas do Pearl (129 valores, cabe em INT8 com folga de 1 bit).
INT8_MIN, INT8_MAX = -128, 127


def naive(a, b, n):
    """Produto exato de matrizes n x n em inteiros Python (referência, sem overflow)."""
    return [[sum(a[i][k] * b[k][j] for k in range(n)) for j in range(n)] for i in range(n)]


def split(m, n):
    """Divide uma matriz 2n x 2n em quatro blocos n x n."""
    top = lambda r: (r[:n], r[n:])
    a11, a12 = zip(*[top(row) for row in m[:n]])
    a21, a22 = zip(*[top(row) for row in m[n:]])
    to = lambda rows: [list(r) for r in rows]
    return to(a11), to(a12), to(a21), to(a22)


def add(x, y, n, sign=1):
    return [[x[i][j] + sign * y[i][j] for j in range(n)] for i in range(n)]


def strassen_one_level(a, b, half, multiply):
    """Um nível de Strassen: 7 produtos de blocos em vez de 8. `multiply` faz cada produto."""
    a11, a12, a21, a22 = split(a, half)
    b11, b12, b21, b22 = split(b, half)
    m1 = multiply(add(a11, a22, half), add(b11, b22, half))
    m2 = multiply(add(a21, a22, half), b11)
    m3 = multiply(a11, add(b12, b22, half, -1))
    m4 = multiply(a22, add(b21, b11, half, -1))
    m5 = multiply(add(a11, a12, half), b22)
    m6 = multiply(add(a21, a11, half, -1), add(b11, b12, half))
    m7 = multiply(add(a12, a22, half, -1), add(b21, b22, half))
    c11 = add(add(add(m1, m4, half), m7, half), m5, half, -1)
    c12 = add(m3, m5, half)
    c21 = add(m2, m4, half)
    c22 = add(add(add(m1, m2, half, -1), m3, half), m6, half)
    out = [[0] * (2 * half) for _ in range(2 * half)]
    for i in range(half):
        for j in range(half):
            out[i][j], out[i][j + half] = c11[i][j], c12[i][j]
            out[i + half][j], out[i + half][j + half] = c21[i][j], c22[i][j]
    return out


def operand_range(a, b, half):
    """Menor e maior valor que aparecem nos operandos dos 7 produtos (para checar INT8)."""
    a11, a12, a21, a22 = split(a, half)
    b11, b12, b21, b22 = split(b, half)
    operands = [add(a11, a22, half), add(b11, b22, half), add(a21, a22, half),
                add(b12, b22, half, -1), add(b21, b11, half, -1), add(a11, a12, half),
                add(a21, a11, half, -1), add(b11, b12, half), add(a12, a22, half, -1),
                add(b21, b22, half)]
    flat = [v for m in operands for row in m for v in row]
    over = sum(1 for v in flat if not INT8_MIN <= v <= INT8_MAX)
    bad_values = sorted({v for v in flat if not INT8_MIN <= v <= INT8_MAX})
    return min(flat), max(flat), over, len(flat), bad_values


def int8_exact_multiply(x, y, half):
    """Produto de blocos com operandos clampados a INT8 e correção exata do caso +128.

    Onde um operando valeria +128, grava 127 e soma o resto (X ou Y) nas posições
    afetadas — um termo de posto 1 em INT32, barato e exato. Demonstra que o único
    estouro possível é recuperável sem perder exatidão.
    """
    def clamp_with_correction(m):
        base = [[min(INT8_MAX, v) for v in row] for row in m]
        extra = [[v - base_v for v, base_v in zip(row, brow)] for row, brow in zip(m, base)]
        return base, extra

    xb, xe = clamp_with_correction(x)
    yb, ye = clamp_with_correction(y)
    # (xb+xe)(yb+ye) = xb*yb + xe*yb + xb*ye + xe*ye; os termos com `extra` só
    # existem nas raras posições ==+128 e são somas inteiras exatas (INT32).
    main = naive(xb, yb, half)
    c1 = naive(xe, yb, half)
    c2 = naive(xb, ye, half)
    c3 = naive(xe, ye, half)
    return add(add(add(main, c1, half), c2, half), c3, half)


def run(sizes, trials, seed):
    rng = random.Random(seed)
    results = []
    for half in sizes:
        n = 2 * half
        exact_ok = overflow_recovered_ok = True
        worst_min, worst_max, total_over, total_lanes = 0, 0, 0, 0
        for _ in range(trials):
            a = [[rng.randint(LOW, HIGH) for _ in range(n)] for _ in range(n)]
            b = [[rng.randint(LOW, HIGH) for _ in range(n)] for _ in range(n)]
            reference = naive(a, b, n)
            ideal = strassen_one_level(a, b, half, lambda x, y: naive(x, y, half))
            recovered = strassen_one_level(a, b, half, lambda x, y: int8_exact_multiply(x, y, half))
            exact_ok &= ideal == reference
            overflow_recovered_ok &= recovered == reference
            lo, hi, over, lanes, _ = operand_range(a, b, half)
            worst_min, worst_max = min(worst_min, lo), max(worst_max, hi)
            total_over += over
            total_lanes += lanes
        results.append({
            "block": half, "matrix": n, "trials": trials,
            "ideal_strassen_exact": exact_ok,
            "int8_overflow_recovered_exact": overflow_recovered_ok,
            "operand_min": worst_min, "operand_max": worst_max,
            "operand_fits_int8_except_plus128": worst_max <= 128 and worst_min >= INT8_MIN,
            "overflow_lane_fraction": total_over / total_lanes,
            "tensor_multiplies": 7, "naive_multiplies": 8,
            "multiply_reduction": 1 - 7 / 8,
        })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = run([2, 4, 8, 16], args.trials, args.seed)
    report = {
        "kind": "CPU exactness/overflow feasibility for 1-level Strassen in INT8; NOT mining, NOT a proof test",
        "input_range": [LOW, HIGH],
        "results": results,
        "all_ideal_exact": all(r["ideal_strassen_exact"] for r in results),
        "all_overflow_recovered": all(r["int8_overflow_recovered_exact"] for r in results),
        "theoretical_speedup_upper_bound": "7 -> 8 multiplies = +14.3%, antes de somas extras e do custo do transcript",
        "blocking_unknown": "O transcript do Pearl pode exigir o produto intermediario de cada fatia de K; "
                            "se exigir, a recombinacao do Strassen nao e aplicavel. Resolver lendo noisy_gemm/inner_hash oficiais.",
    }
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["all_ideal_exact"] and report["all_overflow_recovered"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
