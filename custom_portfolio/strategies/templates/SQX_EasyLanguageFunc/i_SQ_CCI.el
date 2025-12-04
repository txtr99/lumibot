inputs:
	Price( TypicalPrice ) [DisplayName = "Price", ToolTip = 
	 "Enter an EasyLanguage expression to use in the CCI calculations."],
	Length( 14 ) [DisplayName = "Length", ToolTip = 
	 "Enter the number of bars used in the CCI calculation."],
	double OverSold( -100 ) [DisplayName = "OverSold", ToolTip = 
	 "Enter the level of the indicator at which you consider the market to be oversold (too low)."],
	double OverBought( 100 ) [DisplayName = "OverBought", ToolTip = 
	 "Enter the level of the indicator at which you consider the market to be overbought (too high)."] ;
;

variables:  
	CCIValue( 0 ) ;

CCIValue = CCICustom( Price, Length ) ;
 
Plot1( CCIValue, !("CCI" ));
Plot2( 0, !( "ZeroLine" ) ) ;
Plot3( OverBought, !( "OverBot" ) ) ;
Plot4( OverSold, !( "OverSld" ) ) ;
