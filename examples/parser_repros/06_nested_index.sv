// Remaining grammar ambiguity: a multi-level selected member used as an index.
module repro;
  always_comb q[a.b.c.d[N-1:0]] = x;
endmodule
