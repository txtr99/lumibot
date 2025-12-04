Inputs: gam(.5);
Variables: L0(0),
L1(0),
L2(0),
L3(0),
CU(0),
CD(0),
LRSI(0);


L0 = (1 - gam)*Close + gam*L0[1];
L1 = - gam *L0 + L0[1] + gam *L1[1];
L2 = - gam *L1 + L1[1] + gam *L2[1];
L3 = - gam *L2 + L2[1] + gam *L3[1];

CU = 0;
CD = 0;



If L0 >= L1 then CU = L0 - L1 Else CD = L1 - L0;
If L1 >= L2 then CU = CU + L1 - L2 Else CD = CD + L2 - L1;
If L2 >= L3 then CU = CU + L2 - L3 Else CD = CD + L3 - L2;


If CU + CD <> 0 then LRSI = CU / (CU + CD);
Plot1(LRSI, "LaguerreRSI");
Plot2(.9);
Plot3(.1);
