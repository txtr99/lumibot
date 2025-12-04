inputs:
	XMode(1) [DisplayName = "Mode"],
	//Price( Close ) [DisplayName = "Price", ToolTip = "Input Price"],
	Lenght( 3) [DisplayName = "Lenght"],
	ATRPeriod( 3) [DisplayName = "ATRPeriod"];

variables:  
	SQSRPR( 0 ) ;

SQSRPR = SQ_SRPercentRank(XMode,Lenght,ATRPeriod);
 
Plot1( SQSRPR, !("SQ_SRPercentRank"));
