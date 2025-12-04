inputs:
	Price( Close ) [DisplayName = "Price", ToolTip = ""],
	Period( 12 ) [DisplayName = "Period", ToolTip = ""];

variables:  
	LinRegValue( 0 ) ;

LinRegValue = SQ_LinearRegression( Price, Period ) ;
 
Plot1( LinRegValue, !("SQ LinReg" ));
