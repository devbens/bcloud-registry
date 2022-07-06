import React, {Component} from "react";
import {connect} from "react-redux";
import {isEqual, merge} from "lodash";
import * as actions from "../actions";
import MyBreadcrumbs from "./MyBreadcrumbs";
import BoxProviderForm from "./BoxProviderForm";
import {Panel} from "react-bootstrap";
import BoxProviderPageHeader from "./BoxProviderPageHeader";
import {getBoxProvider, getBoxTag} from "../selectors";


class BoxProviderEditPage extends Component {
  state = {
    file: null,
  };

  componentDidMount() {
    if (!this.props.myUsername) {
      this.props.router.push(`/login/?next=${window.location.pathname}`);
      return;
    }
    if (!this.props.boxProvider) {
      this.props.loadBoxVersion(this.props.boxTag, this.props.params.version);
      return;
    }
    this.props.setFormData('boxProvider', this.props.boxProvider);
  }

  componentDidUpdate(prevProps) {
    if (!isEqual(prevProps.boxProvider, this.props.boxProvider)) {
      // For a case when box provider data loaded
      this.props.setFormData('boxProvider', this.props.boxProvider);
    }
  }

  componentWillUnmount() {
    this.props.resetForm('boxProvider');
  }

  onSubmit = (e) => {
    e.preventDefault();
    this.props.editBoxProvider(
        this.props.boxTag,
        this.props.params.version,
        this.props.params.provider,
        merge({}, this.props.form.data, {file: this.state.file})
    );
  };

  onCancel = () => {
    this.props.router.push(
        `/boxes/${this.props.boxTag}/versions/${this.props.params.version}/`
    );
  };

  onFileInputChange = (file) => {
    this.setState({ file });
  };

  render() {
    return (
        <div>
          <BoxProviderPageHeader router={this.props.router} />
          <MyBreadcrumbs router={this.props.router} />
          <Panel header="Edit provider">
            <BoxProviderForm
                pending={this.props.form.pending}
                submitTitle='Save'
                submitPendingTitle="Saving..."
                onSubmit={this.onSubmit}
                onCancel={this.onCancel}
                onFileInputChange={this.onFileInputChange}
            />
          </Panel>
        </div>
    );
  }
}

function mapStateToProps(state, props) {
  return {
    myUsername: state.myUsername,
    form: state.forms.boxProvider,
    boxProvider: getBoxProvider(state, props),
    boxTag: getBoxTag(props),
  }
}

export default connect(mapStateToProps, {
  loadBoxVersion: actions.loadBoxVersion,
  editBoxProvider: actions.editBoxProvider,
  setFormData: actions.form.setData,
  resetForm: actions.form.reset,
})(BoxProviderEditPage)
