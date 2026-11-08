// Grammar reproduction: selected members inside concat, comparison and ternary.
module repro;
  always_comb q[i] = (({a.b[i][13:8], a.b[i][23:22]} != 8'b0) && x[i][j][k]) ? 1'b1 : 1'b0;
endmodule
