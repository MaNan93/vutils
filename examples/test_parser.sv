// Test file for verible_parser.py
// Covers: ANSI, non-ANSI, no ports, multiple instances, localparam, empty body

// ---- Case 1: non-ANSI with multiple instances ----
module top(
    clk,
    rst,
    data_in,
    data_out
);
    input wire clk;
    input wire rst;
    input wire [7:0] data_in;
    output reg [7:0] data_out;

    wire [7:0] sig_a;
    wire [3:0] sig_b;
    reg  [15:0] counter;

    adder u_adder0 (
        .clk(clk),
        .rst(rst),
        .a(data_in),
        .s(sig_a)
    );

    adder u_adder1 (
        .clk(clk),
        .rst(rst),
        .a(sig_b),
        .s(sig_a)
    );

    mul u_mul (
        .a(sig_a),
        .b(sig_b),
        .p(counter)
    );

    always @(posedge clk) begin
        if (rst)
            counter <= 0;
        else
            counter <= counter + 1;
    end
endmodule

// ---- Case 2: ANSI with localparam ----
module alu #(
    parameter WIDTH = 8,
    localparam FULL = WIDTH * 2
)(
    input  wire                clk,
    input  wire [WIDTH-1:0]    a,
    input  wire [WIDTH-1:0]    b,
    input  wire [1:0]          op,
    output reg  [FULL-1:0]     result
);

    always @(posedge clk) begin
        case (op)
            2'b00: result <= a + b;
            2'b01: result <= a - b;
            2'b10: result <= a & b;
            2'b11: result <= a | b;
        endcase
    end
endmodule

// ---- Case 3: module with no ports (only instances) ----
module wrapper;
    logic clk;
    logic rst;

    clk_gen u_clk (
        .clk(clk),
        .rst(rst)
    );
endmodule

// ---- Case 4: ANSI module with no body declarations ----
module passthrough (
    input  wire [7:0] din,
    output wire [7:0] dout
);
    assign dout = din;
endmodule

// ---- Case 5: empty body ----
module empty_mod (
    input  clk,
    output ready
);
endmodule
