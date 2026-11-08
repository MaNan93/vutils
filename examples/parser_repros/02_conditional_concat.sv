// Grammar reproduction: a parenthesized conditional after two selected members.
module repro;
  assign q = {a.b[13:10], a.b[9:8], (c.d[i] ? c.e[i][7:0] : a.b[7:0])};
endmodule
