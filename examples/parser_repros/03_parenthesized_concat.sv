// Grammar reproduction: three hierarchical references in a parenthesized concat.
module repro;
  assign q = ({a.b, c.d, e.f});
endmodule
