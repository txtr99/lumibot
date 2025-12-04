inputs:

	Length(14);


variables:
	VMPlus( 0 ),
	VMMinus( 0 ),
	VMPlusSum( 0 ),
	VMMinusSum( 0 ),
	TR( 0 ),
	TRSum( 0 ),
	VIPlusSumRge( 0 ),
	VIMinusSumRge( 0 ) ;

VMPlus = AbsValue( High - Low[1] ) ;
VMMinus = AbsValue( Low - High[1] ) ;
VMPlusSum = Summation( VMPlus, Length ) ;
VMMinusSum = Summation( VMMinus, Length ) ;
TR = TrueRange ;
TRSum = Summation( TR, Length ) ;

if TRSum <> 0  then
	begin
	VIPlusSumRge = VMPlusSum / TRSum ;
	VIMinusSumRge = VMMinusSum / TRSum ;
	end ;

Plot1( VIPlusSumRge, "VI+Sum/Rge", Green ) ;
Plot2( VIMinusSumRge, "VI-Sum/Rge", Red ) ;
