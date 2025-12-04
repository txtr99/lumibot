inputs:
	Price( Close ) [DisplayName = "Price", ToolTip = "Input Price"],
	Period( 12 ) [DisplayName = "Period", ToolTip = "Period"];

variables:  
	LWMA( 0 ) ;

LWMA = SQ_LinearWAverage(Price, Period);
 
Plot1( LWMA, !("LWMA"));
